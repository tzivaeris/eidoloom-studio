'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { topScores, average, range, rankOf } = require('../src/scores');

test('topScores returns the highest scores first', () => {
  assert.deepStrictEqual(topScores([5, 40, 100, 9], 3), [100, 40, 9]);
});

test('topScores does not change its input', () => {
  const input = [3, 1, 2];
  topScores(input, 2);
  assert.deepStrictEqual(input, [3, 1, 2]);
});

test('average of scores', () => {
  assert.strictEqual(average([2, 4, 6]), 4);
  assert.strictEqual(average([]), 0);
});

test('range includes both ends', () => {
  assert.deepStrictEqual(range(2, 5), [2, 3, 4, 5]);
});

test('rankOf counts better scores', () => {
  assert.strictEqual(rankOf([10, 20, 30], 20), 2);
});
