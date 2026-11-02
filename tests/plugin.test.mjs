import assert from 'node:assert/strict';
import test from 'node:test';
import { setup } from '../plugins/tool-result-compat/index.mjs';

const provider = 'example-provider';
const route = `${provider}/example-model`;
const text = (value) => ({ type: 'text', text: value });
const result = (content) => ({ messages: [{ role: 'user', content: [{ type: 'tool_result', tool_use_id: 'call-1', content }] }] });
const image = () => ({ type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'YQ==' } });

function plugin(config = { providers: [provider] }) {
  const warnings = [];
  const instance = setup({ pluginConfig: config, logger: { warn: (s) => warnings.push(s) } });
  return { ...instance, warnings };
}
async function apply(instance, body, routedModel = route, id = 'strip-tool-images') {
  return instance.gatewayRequestTransforms.find((t) => t.id === id).transform({ body, routedModel });
}
function freeze(value) {
  if (value && typeof value === 'object') { Object.freeze(value); Object.values(value).forEach(freeze); }
  return value;
}

test('host contract exposes both legacy transform IDs', () => {
  assert.deepEqual(plugin().gatewayRequestTransforms.map((t) => t.id), ['strip-tool-images', 'fix-partial-tool-rounds']);
});
test('provider opt-in required, including empty list', async () => {
  for (const config of [{}, { providers: [] }]) assert.equal(await apply(plugin(config), result([image()])), undefined);
});
test('unselected provider remains untouched', async () => {
  assert.equal(await apply(plugin(), result([image()]), 'other-provider/model'), undefined);
});
test('invalid routes do not leak input into warnings', async () => {
  const p = plugin();
  for (const routedModel of [null, '', 'malformed', '/model', `${provider}/`, `${provider}/\u001b[31mPRIVATE`, `${provider}/a/b`]) {
    assert.equal(await apply(p, result([image()]), routedModel), undefined);
  }
  assert.ok(p.warnings.length > 0);
  assert.ok(p.warnings.every((w) => !w.includes('PRIVATE') && !w.includes('\u001b')));
});
test('warnings are rate limited and isolated per instance', async () => {
  const a = plugin(); const b = plugin();
  for (let i = 0; i < 10; i++) await apply(a, {}, 'bad');
  assert.equal(a.warnings.length, 1);
  assert.equal(b.diagnostics.malformedRoutedModel, 0);
  assert.equal(a.diagnostics.malformedRoutedModel, 10);
});
test('configuration rejects unsafe thresholds and unknown transforms', () => {
  for (const config of [{ maxTextPayloadBytes: -1 }, { maxInlinePayloadBytes: Infinity }, { transforms: ['typo'] }, { providers: ['bad/provider'] }]) {
    assert.throws(() => plugin({ providers: [provider], ...config }), TypeError);
  }
});
test('transform filter enables only requested operation', async () => {
  const p = plugin({ providers: [provider], transforms: ['fix-partial-tool-rounds'] });
  assert.equal(await apply(p, result([image()])), undefined);
});
test('ordinary text and large prose keep identity/no-op contract', async () => {
  for (const content of ['hello', [text('hello')], [text('ordinary words. '.repeat(20000))], { type: 'search_result', title: 'text' }]) {
    assert.equal(await apply(plugin(), freeze(result(content))), undefined);
  }
});
test('tool images replaced without mutating frozen input', async () => {
  const body = freeze(result([image(), text('keep')]));
  const changed = await apply(plugin(), body);
  assert.notEqual(changed.body, body);
  assert.deepEqual(changed.body.messages[0].content[0].content, [text('[image omitted]'), text('keep')]);
  assert.equal(body.messages[0].content[0].content[0].type, 'image');
});
for (const type of ['document', 'audio', 'video', 'file', 'container_upload']) {
  test(`typed ${type} omitted in tool result`, async () => {
    const changed = await apply(plugin(), result([{ type, source: { data: 'YQ==' } }]));
    assert.equal(changed.body.messages[0].content[0].content[0].type, 'text');
    assert.match(changed.body.messages[0].content[0].content[0].text, /omitted/);
  });
}
test('top-level user media preserved', async () => {
  assert.equal(await apply(plugin(), { messages: [{ role: 'user', content: [image()] }] }), undefined);
});
test('tool references remain intact', async () => {
  assert.equal(await apply(plugin(), result([{ type: 'tool_reference', tool_name: 'read' }])), undefined);
});
test('null/missing/empty tool result gets placeholder', async () => {
  for (const content of [null, undefined, []]) {
    const changed = await apply(plugin(), result(content));
    assert.deepEqual(changed.body.messages[0].content[0].content, [text('[empty tool result]')]);
  }
});
test('deep structured binary removed but small metadata preserved', async () => {
  const p = plugin({ providers: [provider], maxInlinePayloadBytes: 64 });
  const content = { type: 'search_result', nested: { data: 'A'.repeat(1024) } };
  assert.ok(await apply(p, result([content])));
  assert.equal(await apply(p, result([{ type: 'search_result', nested: { data: 'abc' } }])), undefined);
});
test('large embedded data URL in text omitted', async () => {
  const changed = await apply(plugin(), result([text(`screenshot data:image/png;base64,${'A'.repeat(200000)}`)]));
  assert.deepEqual(changed.body.messages[0].content[0].content, [text('[embedded binary payload omitted]')]);
});
test('structured payload threshold boundary', async () => {
  const p = plugin({ providers: [provider], maxInlinePayloadBytes: 64 });
  assert.equal(await apply(p, result([{ data: 'A'.repeat(63) }])), undefined);
  assert.ok(await apply(p, result([{ data: 'A'.repeat(64) }])));
});
test('full tool round remains unchanged', async () => {
  const body = { messages: [{ role: 'assistant', content: [{ type: 'tool_use', id: 'call-1', name: 'read', input: {} }] }, ...result('done').messages] };
  assert.equal(await apply(plugin(), freeze(body), route, 'fix-partial-tool-rounds'), undefined);
});
test('partial tool round drops pending calls, keeps answered calls and text', async () => {
  const body = freeze({ messages: [{ role: 'assistant', content: [text('start'), { type: 'tool_use', id: 'call-1' }, { type: 'tool_use', id: 'pending' }] }, ...result('done').messages] });
  const changed = await apply(plugin(), body, route, 'fix-partial-tool-rounds');
  assert.deepEqual(changed.body.messages[0].content, [text('start'), { type: 'tool_use', id: 'call-1' }]);
  assert.equal(body.messages[0].content.length, 3);
});
test('all-pending assistant retains placeholder', async () => {
  const changed = await apply(plugin(), { messages: [{ role: 'assistant', content: [{ type: 'tool_use', id: 'pending' }] }] }, route, 'fix-partial-tool-rounds');
  assert.deepEqual(changed.body.messages[0].content, [text('[pending tool calls omitted]')]);
});
test('host-style sequential transforms are idempotent and preserve user image', async () => {
  const p = plugin();
  const input = { messages: [{ role: 'assistant', content: [{ type: 'tool_use', id: 'pending' }] }, ...result([image()]).messages, { role: 'user', content: [image()] }] };
  let body = input;
  for (const t of p.gatewayRequestTransforms) body = (await t.transform({ body, routedModel: route }))?.body ?? body;
  assert.equal(body.messages[2].content[0].type, 'image');
  for (const t of p.gatewayRequestTransforms) assert.equal(await t.transform({ body, routedModel: route }), undefined);
});
test('invalid body shapes safely produce no-op', async () => {
  for (const body of [null, {}, { messages: 'bad' }, { messages: [null, {}] }]) {
    for (const t of plugin().gatewayRequestTransforms) assert.equal(await t.transform({ body, routedModel: route }), undefined);
  }
});
test('pending tool_use inside user messages left untouched', async () => {
  const body = freeze({ messages: [{ role: 'user', content: [{ type: 'tool_use', id: 'odd' }] }] });
  assert.equal(await apply(plugin(), body, route, 'fix-partial-tool-rounds'), undefined);
});
test('duplicate tool_use id in later round stays pending', async () => {
  const body = freeze({ messages: [
    { role: 'assistant', content: [{ type: 'tool_use', id: 'call-1' }] },
    { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'call-1', content: 'ok' }] },
    { role: 'assistant', content: [{ type: 'tool_use', id: 'call-1' }] },
  ] });
  const changed = await apply(plugin(), body, route, 'fix-partial-tool-rounds');
  assert.equal(changed.body.messages.length, 3);
  assert.equal(changed.body.messages[1].content[0].type, 'tool_result');
  assert.deepEqual(changed.body.messages[2].content, [text('[pending tool calls omitted]')]);
});
