import test from 'node:test';
import assert from 'node:assert/strict';
import { createWheelGesture, bindHeroScroll } from '../site/scripts/hero-scroll.js';

test('一次大滑动和持续惯性只能前进一段', () => {
  const gesture = createWheelGesture();
  const steps = [];
  for (let time = 0; time < 2400; time += 20) {
    const step = gesture(time === 0 ? 3600 : 80, time, true);
    if (step) steps.push(step);
  }
  assert.deepEqual(steps, [1]);
  assert.equal(gesture(100, 2800, true), 1, '下一次独立手势才进入下一段');
});

test('镜头未到位时的新手势不能在到位后继续触发切章', () => {
  const gesture = createWheelGesture();
  assert.equal(gesture(100, 0, true), 1);
  assert.equal(gesture(100, 400, false), 0);
  assert.equal(gesture(100, 500, true), 0);
  assert.equal(gesture(100, 900, true), 1);
});

test('轻微滚动不误触，独立向上手势只后退一段', () => {
  const gesture = createWheelGesture();
  assert.equal(gesture(12, 0, true), 0);
  assert.equal(gesture(15, 20, true), 0);
  assert.equal(gesture(15, 40, true), 1);
  assert.equal(gesture(-3600, 500, true), -1);
  assert.equal(gesture(-100, 520, true), 0);
  assert.equal(gesture(NaN, 800, true), 0);
});

test('一次长触摸只切一段，首尾向外移动与双指手势不被拦截', () => {
  const saved = Object.fromEntries(['window', 'scrollY', 'innerHeight'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const target = new EventTarget();
  Object.assign(globalThis, { window:target, scrollY:0, innerHeight:800 });
  let phase = 0;
  const steps = [];
  const dispose = bindHeroScroll({ offsetTop:0, offsetHeight:4000 }, {
    getPhase: () => phase, isSettled: () => true, goTo: next => { phase = next; steps.push(next); },
  });
  const send = (type, y, count = 1) => {
    const event = new Event(type, { cancelable:true });
    Object.defineProperty(event, 'touches', { value:Array.from({ length:count }, () => ({ clientX:200, clientY:y })) });
    target.dispatchEvent(event);
    return event.defaultPrevented;
  };
  try {
    send('touchstart', 600);
    for (const y of [550, 400, 250, 100]) assert.equal(send('touchmove', y), true);
    assert.deepEqual(steps, [1]);
    send('touchend', 0, 0);
    send('touchstart', 600);
    send('touchmove', 500);
    assert.deepEqual(steps, [1, 2]);
    send('touchend', 0, 0);
    phase = 3;
    send('touchstart', 600);
    assert.equal(send('touchmove', 300), false, '最后一段向下交还原生滚动');
    phase = 0;
    send('touchstart', 300);
    assert.equal(send('touchmove', 500), false, '第一段向上交还原生滚动');
    send('touchstart', 600, 2);
    assert.equal(send('touchmove', 300, 2), false);
  } finally {
    dispose();
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
