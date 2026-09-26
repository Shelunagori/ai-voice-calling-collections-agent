// Microphone capture worklet: downsample to 16 kHz mono PCM16 and post 20 ms frames.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.acc = [];
    this.pos = 0;
    this.frame = new Int16Array(320);
    this.fill = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    // Box-filter decimation: average the input samples that fall in each output slot.
    for (let i = 0; i < ch.length; i++) {
      this.acc.push(ch[i]);
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        let sum = 0;
        for (const v of this.acc) sum += v;
        const s = Math.max(-1, Math.min(1, sum / this.acc.length));
        this.acc = [];
        this.frame[this.fill++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        if (this.fill === this.frame.length) {
          this.port.postMessage(this.frame.buffer.slice(0));
          this.fill = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("capture-16k", CaptureProcessor);
