// Pure audio helpers (unit-tested). public/audio-worklet.js mirrors downsampleToInt16.

/** Box-filter decimation from `inRate` to `outRate` with clipping to int16. */
export function downsampleToInt16(input: Float32Array, inRate: number, outRate: number): Int16Array {
  if (!(inRate > 0) || !(outRate > 0) || outRate > inRate) throw new RangeError(`cannot resample ${inRate} -> ${outRate}`);
  const ratio = inRate / outRate;
  const n = Math.floor(input.length / ratio);
  const out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i * ratio);
    const b = Math.min(input.length, Math.max(a + 1, Math.floor((i + 1) * ratio)));
    let sum = 0;
    for (let j = a; j < b; j++) sum += input[j];
    const s = Math.max(-1, Math.min(1, sum / (b - a)));
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}
