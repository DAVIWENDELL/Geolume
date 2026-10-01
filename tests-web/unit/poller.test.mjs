import { test } from "node:test";
import assert from "node:assert/strict";
import { createPoller } from "../../worker/web/js/poller.js";

// Timers falsos: guardam os agendamentos e deixam o teste disparar um por vez.
function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    delays: [],
    pending,
    setTimeout(fn, ms) {
      const id = next++;
      this.delays.push(ms);
      pending.set(id, fn);
      return id;
    },
    clearTimeout(id) {
      pending.delete(id);
    },
    async fire() {
      const [id, fn] = pending.entries().next().value;
      pending.delete(id);
      fn();
      await flush();
    },
  };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test("continua no intervalo enquanto task retorna true", async () => {
  const timers = fakeTimers();
  let runs = 0;
  const poller = createPoller({ task: async () => (runs++, true), interval: 2000, maxInterval: 15000, timers });
  poller.start();
  await flush();
  await timers.fire();
  await timers.fire();
  assert.equal(runs, 3);
  assert.deepEqual(timers.delays, [2000, 2000, 2000]);
  assert.equal(poller.running, true);
});

test("para quando task retorna false", async () => {
  const timers = fakeTimers();
  const poller = createPoller({ task: async () => false, interval: 2000, maxInterval: 15000, timers });
  poller.start();
  await flush();
  assert.equal(timers.pending.size, 0);
  assert.equal(poller.running, false);
});

test("backoff dobra ate o maximo e volta ao intervalo apos sucesso", async () => {
  const timers = fakeTimers();
  const results = ["err", "err", "err", "err", true];
  const poller = createPoller({
    task: async () => {
      const r = results.shift();
      if (r === "err") throw new Error("falhou");
      return r;
    },
    interval: 2000,
    maxInterval: 15000,
    timers,
  });
  poller.start();
  await flush();
  for (let i = 0; i < 4; i++) await timers.fire();
  assert.deepEqual(timers.delays, [4000, 8000, 15000, 15000, 2000]);
});

test("stop durante task pendente nao reagenda", async () => {
  const timers = fakeTimers();
  let release;
  const poller = createPoller({
    task: () => new Promise((resolve) => (release = resolve)),
    interval: 2000,
    maxInterval: 15000,
    timers,
  });
  poller.start();
  poller.stop();
  release(true);
  await flush();
  assert.equal(timers.pending.size, 0);
  assert.equal(poller.running, false);
});

test("start duplo nao duplica execucoes nem timers", async () => {
  const timers = fakeTimers();
  let runs = 0;
  const poller = createPoller({ task: async () => (runs++, true), interval: 2000, maxInterval: 15000, timers });
  poller.start();
  poller.start();
  await flush();
  poller.start();
  assert.equal(runs, 1);
  assert.equal(timers.pending.size, 1);
});

test("stop cancela timer agendado e start retoma imediatamente", async () => {
  const timers = fakeTimers();
  let runs = 0;
  const poller = createPoller({ task: async () => (runs++, true), interval: 2000, maxInterval: 15000, timers });
  poller.start();
  await flush();
  poller.stop();
  assert.equal(timers.pending.size, 0);
  poller.start();
  await flush();
  assert.equal(runs, 2);
  assert.equal(timers.pending.size, 1);
});

test("execução anterior a um reinício fica marcada como obsoleta", async () => {
  const timers = fakeTimers();
  const runs = [];
  const poller = createPoller({
    task: (ctx) => new Promise((resolve) => runs.push({ ctx, resolve })),
    interval: 2000,
    maxInterval: 15000,
    timers,
  });
  poller.start();
  poller.stop();
  poller.start();
  assert.equal(runs.length, 2);
  assert.equal(runs[0].ctx?.isStale(), true);
  assert.equal(runs[1].ctx?.isStale(), false);
  runs[1].resolve(false);
  runs[0].resolve(true);
  await flush();
  assert.equal(poller.running, false);
  assert.equal(timers.pending.size, 0);
});
