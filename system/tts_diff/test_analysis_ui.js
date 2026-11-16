const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const {TextDecoder, TextEncoder} = require('util');
const html = fs.readFileSync('system/tts_generator/tts_generator.html', 'utf8');
function section(start, end) {
    const a = html.indexOf(start), b = html.indexOf(end, a);
    assert(a >= 0 && b > a);
    return html.slice(a, b);
}
const source = section('    function invalidateAnalysis(item)', '    const howlInstances') +
    section("    document.getElementById('saveAndAnalyzeTTS').addEventListener", '    function highlightProblems(');
const result = (id, status = 'review') => ({type:'result', id, status, completed:id, total:2});
const complete = {type:'complete', completed:2, total:2};
const line = value => JSON.stringify(value) + '\n';

function harness(items) {
    const button = {disabled:false, addEventListener(event, fn) { this.click = fn; }};
    const progress = {textContent:''};
    const requests = [];
    let blurred = 0, nextRead;
    const readQueue = [];
    const context = {
        ttsList: items, analysisBusy:false, revisions:new WeakMap(), analysisResults:new Map(), problematicIds:[],
        TextDecoder, Uint8Array, encodeURIComponent,
        document: {querySelector() { return {blur() { blurred++; }}; }, getElementById(id) {
            return id === 'saveAndAnalyzeTTS' ? button : id === 'analysisProgress' ? progress : {value:id === 'AnalyzeAccuracy' ? '97' : 'small'};
        }},
        updateUIList() {},
        fetch: async (url, options) => {
            requests.push({url, body:JSON.parse(options.body)});
            return {ok:true, body:{getReader() {return {read() {
                if (readQueue.length) return Promise.resolve(readQueue.shift());
                return new Promise(resolve => { nextRead = resolve; });
            }}; }}};
        }
    };
    vm.createContext(context); vm.runInContext(source, context);
    function push(value, done = false) {
        const chunk = {value:new TextEncoder().encode(value), done};
        if (nextRead) { const resolve = nextRead; nextRead = null; resolve(chunk); } else readQueue.push(chunk);
    }
    return {context, button, progress, requests, push, get blurred() {return blurred;}};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
async function main() {
    {
        const rows = [{id:1, accepted:false}, {id:2, accepted:true}];
        const h = harness(rows);
        h.button.click(); await tick();
        assert.deepStrictEqual(h.requests[0].body.map(x => x.id), [1]);
        assert.match(h.requests[0].url, /threshold=97&whisper_model=small/);
        assert.strictEqual(h.blurred, 1); assert.strictEqual(h.button.disabled, true);
        h.push(line(result(1))); await tick();
        assert.strictEqual(h.context.analysisResults.get(1).status, 'review');
        assert.strictEqual(h.button.disabled, true);
        h.push(line(complete)); h.push('', true); await tick();
        assert.strictEqual(h.button.disabled, false);
        assert.match(h.progress.textContent, /complete/);
    }
    {
        const rows = [{id:1}, {id:2, accepted:true}];
        const h = harness(rows); const pending = h.context.runAnalysis([rows[1]]); await tick();
        assert.deepStrictEqual(h.requests[0].body.map(x => x.id), [2]);
        h.context.toggleAcceptance(2);
        h.push(line(result(2)) + line(complete)); h.push('', true); await pending;
        assert.strictEqual(rows[1].accepted, true);
        assert.strictEqual(h.context.analysisResults.has(2), false);
    }
    for (const change of ['regenerate', 'replace']) {
        const rows = [{id:1}]; const h = harness(rows);
        const pending = h.context.runAnalysis(rows); await tick();
        if (change === 'regenerate') h.context.invalidateAnalysis(rows[0]);
        else h.context.ttsList = [{id:1}];
        h.push(line(result(1)) + line(complete)); h.push('', true); await pending;
        assert.strictEqual(h.context.analysisResults.has(1), false, change);
    }
    for (const tail of ['{bad json}\n', '']) {
        const rows = [{id:1}, {id:2}]; const h = harness(rows);
        const pending = h.context.runAnalysis(rows); await tick();
        h.push(line(result(1))); await tick();
        h.push(tail, true); await pending;
        assert.strictEqual(h.context.analysisResults.get(1).status, 'review');
        assert.strictEqual(h.context.analysisResults.has(2), false);
        assert.strictEqual(h.button.disabled, false);
        assert.strictEqual(h.context.analysisBusy, false);
        assert.match(h.progress.textContent, /Analysis stopped/);
    }
    console.log('ASR UI behavior tests passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
