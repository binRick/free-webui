<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import {
    listModels,
    getAudioStatus,
    transcribeAudio,
    synthesizeSpeech,
    temporaryChat,
    type ContentPart
  } from '$lib/api';
  import { stripReasoning } from '$lib/reasoning';

  // Hands-free voice/video call: a continuous loop of
  //   listen (STT) → think (stream model) → speak (TTS) → listen …
  // Server STT/TTS proxies are preferred; the browser Web Speech API is the
  // fallback. An optional camera attaches a frame to each turn for vision models.

  type Phase = 'idle' | 'listening' | 'thinking' | 'speaking';
  interface Turn { role: 'user' | 'assistant'; text: string; image?: boolean }

  let models = $state<string[]>([]);
  let model = $state<string | null>(null);
  let sttServer = $state(false);
  let ttsServer = $state(false);
  let ttsVoice = $state<string | null>(null);

  let active = $state(false);
  let phase = $state<Phase>('idle');
  let muted = $state(false);
  let cameraOn = $state(false);
  let transcript = $state<Turn[]>([]);
  let liveReply = $state('');
  // The live bubble shows the answer only — never the raw <think> chain-of-thought.
  let liveReplyText = $derived(stripReasoning(liveReply));
  let liveHeard = $state('');
  let error = $state('');

  // --- media handles (non-reactive) ---
  let micStream: MediaStream | null = null;
  let camStream: MediaStream | null = null;
  let recorder: MediaRecorder | null = null;
  let recChunks: Blob[] = [];
  let audioCtx: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let vadRAF = 0;
  let recognition: any = null;
  let audioEl: HTMLAudioElement | null = null;
  let videoEl: HTMLVideoElement | null = null;
  let stopped = false; // guards async callbacks after endCall()

  const SILENCE_MS = 1100; // trailing silence that ends an utterance
  const MIN_SPEECH_MS = 300; // ignore blips
  const MAX_UTTERANCE_MS = 15000;
  const RMS_THRESHOLD = 0.018;

  const webSpeech = () =>
    (globalThis as any).SpeechRecognition ?? (globalThis as any).webkitSpeechRecognition;

  onMount(async () => {
    models = await listModels();
    model = models[0] ?? null;
    const a = await getAudioStatus();
    sttServer = a.stt;
    ttsServer = a.tts;
    ttsVoice = a.voice;
  });

  onDestroy(() => endCall());

  async function startCall() {
    error = '';
    if (!model) {
      error = 'no model available';
      return;
    }
    if (!sttServer && !webSpeech()) {
      error = 'this browser has no speech recognition; configure a server STT backend or use Chrome';
      return;
    }
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true }
      });
    } catch {
      error = 'microphone unavailable';
      return;
    }
    if (cameraOn) await enableCamera();
    stopped = false;
    active = true;
    transcript = [];
    beginListening();
  }

  function endCall() {
    stopped = true;
    active = false;
    phase = 'idle';
    cancelAnimationFrame(vadRAF);
    try { recorder?.state !== 'inactive' && recorder?.stop(); } catch { /* noop */ }
    recorder = null;
    try { recognition?.stop(); } catch { /* noop */ }
    recognition = null;
    if (audioEl) { audioEl.pause(); audioEl = null; }
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    audioCtx?.close().catch(() => {});
    audioCtx = null;
    analyser = null;
    micStream?.getTracks().forEach((t) => t.stop());
    micStream = null;
    disableCamera();
  }

  // ---- listening ----

  function beginListening() {
    if (stopped || !active) return;
    liveHeard = '';
    phase = 'listening';
    if (muted) return; // stay in listening state but capture nothing
    if (sttServer) startVadRecording();
    else startWebSpeech();
  }

  // Server STT: record until a trailing silence, then transcribe the clip.
  function startVadRecording() {
    if (!micStream) return;
    try {
      recorder = new MediaRecorder(micStream);
    } catch {
      error = 'recording unsupported';
      return;
    }
    recChunks = [];
    recorder.ondataavailable = (e) => e.data.size && recChunks.push(e.data);
    recorder.onstop = async () => {
      cancelAnimationFrame(vadRAF);
      const blob = new Blob(recChunks, { type: recorder?.mimeType || 'audio/webm' });
      recorder = null;
      if (stopped) return;
      if (!blob.size) return beginListening();
      try {
        const text = (await transcribeAudio(blob)).trim();
        handleUtterance(text);
      } catch {
        if (!stopped) beginListening();
      }
    };
    recorder.start();
    runVad();
  }

  // Watch mic RMS: once speech has been detected, end the clip after a gap of
  // silence (or a hard cap), so turn-taking is hands-free.
  function runVad() {
    if (!micStream) return;
    audioCtx = new AudioContext();
    const src = audioCtx.createMediaStreamSource(micStream);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    src.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const startedAt = performance.now();
    let speechStart = 0;
    let lastLoud = 0;

    const tick = () => {
      if (stopped || !analyser || !recorder) return;
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      const now = performance.now();
      if (rms > RMS_THRESHOLD) {
        if (!speechStart) speechStart = now;
        lastLoud = now;
      }
      const spoke = speechStart && now - speechStart > MIN_SPEECH_MS;
      const silent = lastLoud && now - lastLoud > SILENCE_MS;
      if ((spoke && silent) || now - startedAt > MAX_UTTERANCE_MS) {
        if (recorder?.state !== 'inactive') recorder?.stop();
        return;
      }
      vadRAF = requestAnimationFrame(tick);
    };
    vadRAF = requestAnimationFrame(tick);
  }

  // Web Speech fallback: continuous recognition with native endpointing.
  function startWebSpeech() {
    const SR = webSpeech();
    if (!SR) return;
    recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = navigator.language || 'en-US';
    let finalText = '';
    recognition.onresult = (e: any) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t;
        else interim += t;
      }
      liveHeard = (finalText + interim).trim();
    };
    recognition.onerror = () => { recognition = null; if (!stopped) beginListening(); };
    recognition.onend = () => {
      recognition = null;
      if (stopped) return;
      handleUtterance(finalText.trim());
    };
    try { recognition.start(); } catch { /* already started */ }
  }

  // ---- think + speak ----

  async function handleUtterance(text: string) {
    if (stopped) return;
    if (!text) return beginListening();
    const frame = cameraOn ? captureFrame() : null;
    transcript = [...transcript, { role: 'user', text, image: !!frame }];
    liveHeard = '';
    phase = 'thinking';
    liveReply = '';

    // Replay the text transcript; attach the camera frame (if any) only to the
    // final user turn so the payload stays bounded.
    const msgs = transcript.map((t) => ({
      role: t.role,
      content: t.text as string | ContentPart[]
    }));
    if (frame) {
      msgs[msgs.length - 1] = {
        role: 'user',
        content: [
          { type: 'text', text },
          { type: 'image_url', image_url: { url: frame } }
        ]
      };
    }

    try {
      await temporaryChat(msgs, model, { onDelta: (d) => (liveReply += d) });
    } catch {
      liveReply = liveReply || '(no response)';
    }
    if (stopped) return;
    // Strip chain-of-thought: a reasoning model's <think> must not be spoken
    // aloud nor replayed back on the next turn (transcript feeds the next call).
    const reply = stripReasoning(liveReply) || '(no response)';
    transcript = [...transcript, { role: 'assistant', text: reply }];
    liveReply = '';
    await speak(reply);
    if (!stopped) beginListening();
  }

  function speak(text: string): Promise<void> {
    return new Promise((resolve) => {
      if (stopped) return resolve();
      phase = 'speaking';
      if (ttsServer) {
        synthesizeSpeech(text, ttsVoice ?? undefined)
          .then((blob) => {
            if (stopped) return resolve();
            const url = URL.createObjectURL(blob);
            audioEl = new Audio(url);
            const done = () => { URL.revokeObjectURL(url); audioEl = null; resolve(); };
            audioEl.onended = done;
            audioEl.onerror = done;
            audioEl.play().catch(done);
          })
          .catch(() => resolve());
        return;
      }
      if (!('speechSynthesis' in window)) return resolve();
      const u = new SpeechSynthesisUtterance(text);
      u.onend = () => resolve();
      u.onerror = () => resolve();
      speechSynthesis.speak(u);
    });
  }

  // ---- camera ----

  async function enableCamera() {
    try {
      camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } });
      if (videoEl) {
        videoEl.srcObject = camStream;
        await videoEl.play().catch(() => {});
      }
    } catch {
      cameraOn = false;
      error = 'camera unavailable';
    }
  }

  function disableCamera() {
    camStream?.getTracks().forEach((t) => t.stop());
    camStream = null;
    if (videoEl) videoEl.srcObject = null;
  }

  async function toggleCamera() {
    cameraOn = !cameraOn;
    if (!active) return;
    if (cameraOn) await enableCamera();
    else disableCamera();
  }

  function captureFrame(): string | null {
    if (!videoEl || !videoEl.videoWidth) return null;
    const canvas = document.createElement('canvas');
    const w = Math.min(videoEl.videoWidth, 768);
    const scale = w / videoEl.videoWidth;
    canvas.width = w;
    canvas.height = Math.round(videoEl.videoHeight * scale);
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.6);
  }

  function toggleMute() {
    muted = !muted;
    if (!active) return;
    if (muted) {
      cancelAnimationFrame(vadRAF);
      try { recorder?.state !== 'inactive' && recorder?.stop(); } catch { /* noop */ }
      try { recognition?.stop(); } catch { /* noop */ }
    } else if (phase === 'listening') {
      beginListening();
    }
  }

  const phaseLabel = $derived(
    phase === 'listening' ? (muted ? 'muted' : 'listening…')
    : phase === 'thinking' ? 'thinking…'
    : phase === 'speaking' ? 'speaking…'
    : 'ready'
  );
</script>

<svelte:head><title>call · free-webui</title></svelte:head>

<div class="call">
  <header>
    <a class="back" href="/">←</a>
    <span class="title">📞 voice call</span>
    {#if active}<span class="phase {phase}">{phaseLabel}</span>{/if}
    <div class="spacer"></div>
    {#if !active}
      <select bind:value={model} class="model">
        {#each models as m (m)}<option value={m}>{m}</option>{/each}
      </select>
    {/if}
  </header>

  {#if error}<div class="err">{error}</div>{/if}

  <div class="stage">
    <!-- self-view: always mounted so captureFrame() has the element -->
    <div class="selfview" class:on={cameraOn}>
      <!-- svelte-ignore a11y_media_has_caption -->
      <video bind:this={videoEl} muted playsinline></video>
    </div>

    <div class="orb {phase}" aria-hidden="true">
      <div class="ring"></div>
      <span class="emoji">{phase === 'speaking' ? '🔊' : phase === 'thinking' ? '💭' : '🎙'}</span>
    </div>

    <div class="transcript">
      {#each transcript as turn, i (i)}
        <div class="turn {turn.role}">
          <span class="who">{turn.role === 'user' ? 'you' : 'assistant'}</span>
          <span class="text">{turn.text}{#if turn.image} 📷{/if}</span>
        </div>
      {/each}
      {#if liveHeard}<div class="turn user live"><span class="who">you</span><span class="text">{liveHeard}</span></div>{/if}
      {#if liveReplyText}<div class="turn assistant live"><span class="who">assistant</span><span class="text">{liveReplyText}</span></div>{/if}
      {#if transcript.length === 0 && !liveHeard && !liveReply}
        <p class="hint">
          {active ? 'start speaking — I’ll reply out loud.' : 'press “start call”, then just talk. I listen, answer, and speak back.'}
        </p>
      {/if}
    </div>
  </div>

  <div class="controls">
    {#if !active}
      <button class="start" onclick={startCall} disabled={!model}>📞 start call</button>
      <button class="toggle" class:on={cameraOn} onclick={toggleCamera}>{cameraOn ? '📷 camera on' : '📷 camera off'}</button>
    {:else}
      <button class="toggle" class:on={muted} onclick={toggleMute}>{muted ? '🔇 unmute' : '🎙 mute'}</button>
      <button class="toggle" class:on={cameraOn} onclick={toggleCamera}>{cameraOn ? '📷 camera on' : '📷 camera off'}</button>
      <button class="end" onclick={endCall}>✕ end call</button>
    {/if}
  </div>
</div>

<style>
  .call { display: flex; flex-direction: column; height: 100%; min-height: 0; width: 100%; }
  header {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.75rem 1rem; border-bottom: 1px solid var(--border-soft);
  }
  .back { color: var(--text-dim); text-decoration: none; font-size: 1.1rem; }
  .back:hover { color: var(--text); }
  .title { font-weight: 600; }
  .phase {
    font-size: 0.75rem; padding: 0.15rem 0.5rem; border-radius: 999px;
    background: var(--bg-elev); color: var(--text-muted); border: 1px solid var(--border-soft);
  }
  .phase.listening { color: var(--accent); border-color: var(--accent); }
  .phase.speaking { color: #4caf80; border-color: #4caf80; }
  .spacer { flex: 1; }
  .model {
    background: var(--bg-elev); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.3rem 0.5rem; font: inherit; max-width: 14rem;
  }
  .err {
    margin: 0.6rem 1rem 0; padding: 0.5rem 0.7rem; border-radius: 6px;
    background: color-mix(in srgb, #d8584a 16%, var(--bg-elev)); border: 1px solid #d8584a;
    color: var(--text); font-size: 0.85rem;
  }
  .stage {
    flex: 1; min-height: 0; display: flex; flex-direction: column; align-items: center;
    gap: 1rem; padding: 1.25rem; overflow-y: auto; position: relative;
  }
  .selfview {
    position: absolute; top: 1rem; right: 1rem; width: 150px; aspect-ratio: 4/3;
    border-radius: 10px; overflow: hidden; border: 1px solid var(--border);
    background: #000; display: none;
  }
  .selfview.on { display: block; }
  .selfview video { width: 100%; height: 100%; object-fit: cover; transform: scaleX(-1); }
  .orb { position: relative; width: 130px; height: 130px; display: grid; place-items: center; flex: none; margin-top: 1rem; }
  .orb .ring {
    position: absolute; inset: 0; border-radius: 50%;
    background: radial-gradient(circle, color-mix(in srgb, var(--accent) 30%, transparent), transparent 70%);
    border: 2px solid var(--border);
  }
  .orb .emoji { font-size: 2.6rem; z-index: 1; }
  .orb.listening .ring { border-color: var(--accent); animation: pulse 1.4s ease-in-out infinite; }
  .orb.speaking .ring { border-color: #4caf80; animation: pulse 0.8s ease-in-out infinite; }
  .orb.thinking .ring { animation: spin 1.6s linear infinite; border-top-color: var(--accent); }
  @keyframes pulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.12); opacity: 0.7; } }
  @keyframes spin { to { transform: rotate(360deg); } }
  .transcript {
    width: 100%; max-width: 44rem; display: flex; flex-direction: column; gap: 0.5rem;
  }
  .turn { display: flex; gap: 0.5rem; align-items: baseline; }
  .turn .who {
    flex: none; width: 4.5rem; text-align: right; font-size: 0.72rem;
    color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em;
  }
  .turn .text { overflow-wrap: anywhere; white-space: pre-wrap; }
  .turn.assistant .text { color: var(--text); }
  .turn.user .text { color: var(--text-dim); }
  .turn.live .text { opacity: 0.7; font-style: italic; }
  .hint { color: var(--text-muted); text-align: center; margin-top: 1rem; }
  .controls {
    display: flex; gap: 0.6rem; justify-content: center; flex-wrap: wrap;
    padding: 0.9rem 1rem 1.2rem; border-top: 1px solid var(--border-soft);
  }
  .controls button {
    font: inherit; border-radius: 999px; padding: 0.5rem 1.1rem; cursor: pointer;
    border: 1px solid var(--border); background: var(--bg-elev); color: var(--text);
  }
  .controls .start { background: var(--accent); color: #fff; border-color: var(--accent); font-weight: 600; }
  .controls .end { border-color: #d8584a; color: #d8584a; }
  .controls .toggle.on { border-color: var(--accent); color: var(--accent); }
  .controls button:disabled { opacity: 0.5; cursor: default; }
</style>
