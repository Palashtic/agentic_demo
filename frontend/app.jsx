const { useEffect, useState } = React;

function App() {
  const [backendBase, setBackendBase] = useState(null);
  const [backendStatus, setBackendStatus] = useState("detecting");
  const [backendError, setBackendError] = useState(null);

  const [running, setRunning] = useState(false);
  const [transcriptText, setTranscriptText] = useState("");
  const [file, setFile] = useState(null);
  const [audioFile, setAudioFile] = useState(null);
  const [recording, setRecording] = useState(false);
  const [recordedUrl, setRecordedUrl] = useState(null);
  const [sttStatus, setSttStatus] = useState(null);
  const [dictating, setDictating] = useState(false);
  const [dictationStatus, setDictationStatus] = useState(null);
  const [analystOut, setAnalystOut] = useState(null);
  const [researchOut, setResearchOut] = useState(null);
  const [emailOut, setEmailOut] = useState(null);
  const [pipelineError, setPipelineError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function supportsResearcher(base) {
      try {
        const resp = await fetch(`${base}/openapi.json`, { cache: "no-store" });
        if (!resp.ok) return false;
        const text = await resp.text();
        return text.includes("/agent/researcher/match");
      } catch (e) {
        return false;
      }
    }

    async function detect() {
      setBackendStatus("detecting");
      setBackendError(null);

      const origin = window.location.origin;
      const candidates = [origin, "http://127.0.0.1:8001", "http://127.0.0.1:8000"];
      for (const base of candidates) {
        const ok = await supportsResearcher(base);
        if (ok) {
          if (!cancelled) {
            setBackendBase(base);
            setBackendStatus("ok");
          }
          return;
        }
      }

      if (!cancelled) {
        setBackendStatus("error");
        setBackendError("Backend not detected. Start uvicorn (recommended port 8001).");
      }
    }

    detect();
    return () => {
      cancelled = true;
      try {
        if (window.__speechRec && window.__speechRec.stop) window.__speechRec.stop();
      } catch {}
    };
  }, []);

  async function callAnalyst() {
    const form = new FormData();
    if (file) form.append("file", file, file.name);
    if (transcriptText) form.append("transcript_text", transcriptText);

    const resp = await fetch(`${backendBase}/agent/analyst/analyze`, { method: "POST", body: form });
    const data = await resp.json();
    return { ok: resp.ok && !data.error, data };
  }

  async function callSttTranscribe(audio) {
    const form = new FormData();
    form.append("file", audio, audio.name || "audio.webm");
    const resp = await fetch(`${backendBase}/stt/transcribe`, { method: "POST", body: form });
    let data = null;
    try {
      data = await resp.json();
    } catch (e) {
      data = { error: await resp.text() };
    }
    return { ok: resp.ok && !data.error, data };
  }

  async function callResearcher(analystObj) {
    const params = new URLSearchParams();
    params.set("analyst_json", JSON.stringify(analystObj));

    const response = await fetch(`${backendBase}/agent/researcher/match`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString()
    });
    const data = await response.json();
    return { ok: response.ok && !data.error, data };
  }

  async function callCloser(analystObj, researchObj) {
    const params = new URLSearchParams();
    params.set("analyst_json", JSON.stringify(analystObj));
    params.set("research_output", JSON.stringify(researchObj));

    const resp = await fetch(`${backendBase}/agent/closer/email`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString()
    });
    const data = await resp.json();
    return { ok: resp.ok && !data.error, data };
  }

  async function runPipeline() {
    setRunning(true);
    setPipelineError(null);
    setAnalystOut(null);
    setResearchOut(null);
    setEmailOut(null);
    setSttStatus(null);

    try {
      if (!backendBase) {
        setPipelineError({ error: "Backend not ready. Start the backend and refresh." });
        return;
      }

      // If user provided audio but no text/file transcript, transcribe first.
      if (!transcriptText.trim() && !file && audioFile) {
        setSttStatus("transcribing");
        const t = await callSttTranscribe(audioFile);
        if (!t.ok) {
          setPipelineError(t.data);
          setSttStatus("error");
          return;
        }
        const tText = t.data.transcript_text || "";
        setTranscriptText(tText);
        setSttStatus("ok");
      }

      const a = await callAnalyst();
      if (!a.ok) {
        setPipelineError(a.data);
        return;
      }
      setAnalystOut(a.data);

      const r = await callResearcher(a.data);
      if (!r.ok) {
        setPipelineError(r.data);
        return;
      }
      setResearchOut(r.data);

      const c = await callCloser(a.data, r.data);
      if (!c.ok) {
        setPipelineError(c.data);
        return;
      }
      setEmailOut(c.data);
    } catch (e) {
      setPipelineError({ error: e.message });
    } finally {
      setRunning(false);
    }
  }

  async function startRecording() {
    setPipelineError(null);
    setSttStatus(null);
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setPipelineError({ error: "Recording not supported in this browser." });
      return;
    }
    if (recordedUrl) {
      try {
        URL.revokeObjectURL(recordedUrl);
      } catch {}
      setRecordedUrl(null);
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [];
    const rec = new MediaRecorder(stream);

    rec.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };

    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
      const url = URL.createObjectURL(blob);
      setRecordedUrl(url);
      const f = new File([blob], "recording.webm", { type: blob.type });
      setAudioFile(f);
      setRecording(false);
    };

    rec.start();
    window.__rec = rec;
    setRecording(true);
  }

  function stopRecording() {
    try {
      const rec = window.__rec;
      if (rec && rec.state !== "inactive") rec.stop();
    } catch (e) {
      setRecording(false);
    }
  }

  function startDictation() {
    setPipelineError(null);
    setDictationStatus(null);

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setDictationStatus("Speech recognition not supported in this browser. Try Chrome/Edge, or use audio recording + transcription.");
      return;
    }

    try {
      if (window.__speechRec && window.__speechRec.stop) window.__speechRec.stop();
    } catch {}

    const rec = new SpeechRecognition();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";

    let finalBuffer = "";

    rec.onstart = () => {
      setDictating(true);
      setDictationStatus("Listening...");
    };

    rec.onerror = (e) => {
      setDictationStatus(`Dictation error: ${e && e.error ? e.error : "unknown"}`);
      setDictating(false);
    };

    rec.onend = () => {
      setDictating(false);
      setDictationStatus("Stopped.");
    };

    rec.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const res = event.results[i];
        const text = res[0] && res[0].transcript ? res[0].transcript : "";
        if (res.isFinal) finalBuffer += text;
        else interim += text;
      }

      if (finalBuffer.trim()) {
        const toAppend = finalBuffer.trim().replace(/\s+/g, " ");
        finalBuffer = "";
        setTranscriptText((prev) => {
          const base = prev || "";
          const sep = base.trim() ? "\n" : "";
          return base + sep + toAppend;
        });
      }

      if (interim.trim()) {
        setDictationStatus(`Listening... (${interim.trim()})`);
      } else {
        setDictationStatus("Listening...");
      }
    };

    window.__speechRec = rec;
    try {
      rec.start();
    } catch (e) {
      setDictationStatus("Unable to start dictation. Try again or refresh the page.");
      setDictating(false);
    }
  }

  function stopDictation() {
    try {
      if (window.__speechRec && window.__speechRec.stop) window.__speechRec.stop();
    } catch {}
  }

  async function copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      // Best-effort: clipboard may be blocked on some setups.
      alert("Copy failed. Select the text and copy manually.");
    }
  }

  return (
    <main style={{ maxWidth: 980, margin: "40px auto", padding: 16, fontFamily: "Segoe UI, Tahoma, sans-serif" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div style={{ color: "#475569", fontSize: 13 }}>
          Backend:{" "}
          {backendStatus === "ok" ? (
            <code>{backendBase}</code>
          ) : backendStatus === "detecting" ? (
            "detecting..."
          ) : (
            <span style={{ color: "#b91c1c" }}>{backendError}</span>
          )}
        </div>
        {backendStatus !== "ok" ? (
          <button onClick={() => window.location.reload()} style={{ padding: "6px 10px", cursor: "pointer" }}>
            Retry Detect
          </button>
        ) : null}
      </div>
      <h1>Sales Call Pipeline (Agents 1-3)</h1>
      <p style={{ marginTop: 6 }}>
        Provide a transcript once. The app runs Analyst -> Researcher -> Closer automatically and shows all outputs plus the email.
      </p>

      <section style={{ display: "grid", gap: 12, marginTop: 16 }}>
        <div>
          <label style={{ display: "block", marginBottom: 6, fontWeight: 600 }}>Transcript File</label>
          <input
            type="file"
            accept=".pdf,.docx,.txt"
            onChange={(e) => setFile(e.target.files && e.target.files[0] ? e.target.files[0] : null)}
          />
          <div style={{ marginTop: 6, color: "#475569", fontSize: 13 }}>
            {file ? `Selected: ${file.name}` : "No file selected."}
          </div>
        </div>

        <div>
          <label style={{ display: "block", marginBottom: 6, fontWeight: 600 }}>Voice (Record or Upload)</label>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <button
              onClick={recording ? stopRecording : startRecording}
              style={{ padding: "8px 12px", cursor: "pointer" }}
              disabled={backendStatus !== "ok"}
            >
              {recording ? "Stop Recording" : "Start Recording"}
            </button>
            <button
              onClick={dictating ? stopDictation : startDictation}
              style={{ padding: "8px 12px", cursor: "pointer" }}
              disabled={backendStatus !== "ok"}
            >
              {dictating ? "Stop Dictation (Text)" : "Start Dictation (Text)"}
            </button>
            <input
              type="file"
              accept="audio/*"
              onChange={(e) => setAudioFile(e.target.files && e.target.files[0] ? e.target.files[0] : null)}
            />
            <span style={{ color: "#475569", fontSize: 13 }}>
              {audioFile ? `Audio ready: ${audioFile.name}` : "No audio selected."}
            </span>
          </div>
          {recordedUrl ? (
            <audio controls src={recordedUrl} style={{ marginTop: 8, width: "100%" }} />
          ) : null}
          <div style={{ marginTop: 6, color: "#64748b", fontSize: 13 }}>
            If you provide audio, the app will transcribe it via `POST /stt/transcribe` (requires `OPENAI_API_KEY`).
          </div>
          {sttStatus ? (
            <div style={{ marginTop: 6, color: "#475569", fontSize: 13 }}>
              STT: {sttStatus}
            </div>
          ) : null}
          {dictationStatus ? (
            <div style={{ marginTop: 6, color: "#475569", fontSize: 13 }}>
              Dictation: {dictationStatus}
            </div>
          ) : null}
        </div>

        <div>
          <label style={{ display: "block", marginBottom: 6, fontWeight: 600 }}>Or Paste Transcript Text</label>
          <textarea
            value={transcriptText}
            onChange={(e) => setTranscriptText(e.target.value)}
            rows={10}
            placeholder="Paste transcript here..."
            style={{ width: "100%", padding: 10, borderRadius: 8, border: "1px solid #cbd5e1", fontFamily: "inherit" }}
          />
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button
            onClick={runPipeline}
            disabled={running || backendStatus !== "ok" || (!file && !transcriptText.trim() && !audioFile)}
            style={{ padding: "10px 14px", cursor: "pointer" }}
          >
            {running ? "Running..." : "Run Pipeline"}
          </button>
          <span style={{ color: "#64748b", fontSize: 13 }}>
            Uses: <code>/agent/analyst/analyze</code>, <code>/agent/researcher/match</code>, <code>/agent/closer/email</code>
          </span>
        </div>
      </section>

      {pipelineError ? (
        <pre style={{ marginTop: 16, background: "#3f1d1d", color: "#fee2e2", padding: 12, borderRadius: 8, overflowX: "auto" }}>
          {JSON.stringify(pipelineError, null, 2)}
        </pre>
      ) : null}

      <hr style={{ margin: "28px 0", border: 0, borderTop: "1px solid #e2e8f0" }} />

      <h2 style={{ margin: 0 }}>Agent 1 Output (Analyst)</h2>
      <pre style={{ marginTop: 10, background: "#0f172a", color: "#e2e8f0", padding: 12, borderRadius: 8, overflowX: "auto" }}>
        {analystOut ? JSON.stringify(analystOut, null, 2) : "No result yet."}
      </pre>

      <h2 style={{ margin: "18px 0 0" }}>Agent 2 Output (Researcher)</h2>
      <pre style={{ marginTop: 10, background: "#0f172a", color: "#e2e8f0", padding: 12, borderRadius: 8, overflowX: "auto" }}>
        {researchOut ? JSON.stringify(researchOut, null, 2) : "No result yet."}
      </pre>

      <h2 style={{ margin: "18px 0 0" }}>Agent 3 Output (Closer Email)</h2>
      {emailOut ? (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <strong>Subject:</strong>
            <code style={{ color: "#0f172a" }}>{emailOut.subject || "(empty)"}</code>
            <button onClick={() => copyToClipboard(emailOut.subject || "")} style={{ padding: "6px 10px", cursor: "pointer" }}>
              Copy Subject
            </button>
            <button onClick={() => copyToClipboard(emailOut.body || "")} style={{ padding: "6px 10px", cursor: "pointer" }}>
              Copy Body
            </button>
          </div>
          <pre style={{ marginTop: 10, background: "#0f172a", color: "#e2e8f0", padding: 12, borderRadius: 8, overflowX: "auto", whiteSpace: "pre-wrap" }}>
            {emailOut.body || "(empty)"}
          </pre>
        </div>
      ) : (
        <pre style={{ marginTop: 10, background: "#0f172a", color: "#e2e8f0", padding: 12, borderRadius: 8, overflowX: "auto" }}>
          No result yet.
        </pre>
      )}
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
