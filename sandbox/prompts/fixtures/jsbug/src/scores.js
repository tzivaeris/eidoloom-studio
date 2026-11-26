'use strict';

// Highest scores first.
function topScores(scores, count) {
  const sorted = [...scores].sort();
  return sorted.reverse().slice(0, count);
}

function average(scores) {
  if (scores.length === 0) return 0;
  return scores.reduce((sum, s) => sum + s, 0) / scores.length;
}

// Whole numbers from start to end, both included.
function range(start, end) {
  const out = [];
  for (let i = start; i <= end; i++) out.push(i);
  return out;
}

function rankOf(scores, score) {
  return scores.filter((s) => s > score).length + 1;
}

module.exports = { topScores, average, range, rankOf };
