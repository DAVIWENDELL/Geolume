// Agendador de consultas: um ciclo por vez, sem sobreposição.
// `task` resolve true para continuar e false para parar; exceção aplica backoff.
// `task` recebe { isStale }: true se o poller foi parado ou reiniciado durante a
// execução — o resultado deve ser descartado sem tocar em estado ou DOM.

export function createPoller({ task, interval, maxInterval, timers = globalThis }) {
  let timer = null;
  let running = false;
  let generation = 0;
  let delay = interval;

  function schedule(ms, gen) {
    timer = timers.setTimeout(() => {
      timer = null;
      run(gen);
    }, ms);
  }

  async function run(gen) {
    let keepGoing;
    try {
      keepGoing = await task({ isStale: () => gen !== generation });
      delay = interval;
    } catch {
      delay = Math.min(delay * 2, maxInterval);
      keepGoing = true;
    }
    if (gen !== generation) return; // parado ou reiniciado enquanto a task rodava
    if (keepGoing) schedule(delay, gen);
    else running = false;
  }

  return {
    start() {
      if (running) return;
      running = true;
      delay = interval;
      run(++generation);
    },
    stop() {
      running = false;
      generation += 1;
      if (timer !== null) timers.clearTimeout(timer);
      timer = null;
    },
    get running() {
      return running;
    },
  };
}
