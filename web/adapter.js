// Future adapters must use a controlled backend; never put provider credentials here.
export class VoiceServiceAdapter {
  capabilities() { return {backend:false,asr:false,tts:false,audio:false}; }
  async start() { throw new Error('真实语音服务未配置'); }
  async stop() {}
  async setMuted() { throw new Error('没有活动音频流'); }
  // Future backend providers: breezeblue | local. Provider selection belongs on server.
}
export const voiceService = new VoiceServiceAdapter();
