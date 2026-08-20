import { useState } from "react";
import type { Capabilities, CreateRunRequest, Difficulty, Freshness, Language } from "../../api/types";

interface Props {
  capabilities: Capabilities | null;
  busy: boolean;
  onSubmit: (payload: CreateRunRequest) => void;
}

export function RunForm({ capabilities, busy, onSubmit }: Props) {
  const outOfRuns = capabilities?.runs_remaining_today === 0;
  const [topic, setTopic] = useState("");
  const [language, setLanguage] = useState<Language>("tr");
  const [difficulty, setDifficulty] = useState<Difficulty>("mixed");
  const [maxDuration, setMaxDuration] = useState(60);
  const [freshness, setFreshness] = useState<Freshness>("balanced");
  const [includeEnglish, setIncludeEnglish] = useState(true);
  const [enableAsr, setEnableAsr] = useState(false);
  const [enableStudyNotes, setEnableStudyNotes] = useState(false);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!topic.trim()) return;
    onSubmit({
      topic: topic.trim(),
      filters: {
        language,
        difficulty,
        max_duration_minutes: maxDuration,
        freshness_preference: freshness,
        // Ingilizce genisletme yalnizca ana dil Ingilizce degilken anlamli.
        include_english: language === "en" ? false : includeEnglish,
      },
      options: { enable_asr_fallback: enableAsr, enable_study_notes: enableStudyNotes },
    });
  };

  return (
    <form className="card" onSubmit={submit}>
      <h3>Playlist oluştur</h3>
      <p className="muted">
        Tek bir öğrenme hedefi girin. Konu alt başlıklara ayrılır, her biri için aday havuzu
        toplanır ve videolar önce metadata'ya göre sıralanır.
      </p>

      <label htmlFor="topic">Konu</label>
      <input
        id="topic"
        type="text"
        value={topic}
        placeholder="Örnek: Makine öğrenmesi ile zaman serisi tahmini"
        onChange={(event) => setTopic(event.target.value)}
      />

      <div className="grid" style={{ marginTop: "1rem" }}>
        <div>
          <label htmlFor="language">Dil</label>
          <select id="language" value={language} onChange={(e) => setLanguage(e.target.value as Language)}>
            <option value="tr">Türkçe</option>
            <option value="en">English</option>
          </select>
        </div>
        <div>
          <label htmlFor="difficulty">Seviye</label>
          <select id="difficulty" value={difficulty} onChange={(e) => setDifficulty(e.target.value as Difficulty)}>
            <option value="mixed">Karışık</option>
            <option value="beginner">Başlangıç</option>
            <option value="intermediate">Orta</option>
            <option value="advanced">İleri</option>
          </select>
        </div>
        <div>
          <label htmlFor="freshness">Tazelik</label>
          <select id="freshness" value={freshness} onChange={(e) => setFreshness(e.target.value as Freshness)}>
            <option value="balanced">Dengeli</option>
            <option value="evergreen">Kalıcı içerik</option>
            <option value="recent">Güncel</option>
          </select>
        </div>
        <div>
          <label htmlFor="duration">En fazla süre: {maxDuration} dk</label>
          <input
            id="duration"
            type="range"
            min={10}
            max={180}
            step={5}
            value={maxDuration}
            onChange={(event) => setMaxDuration(Number(event.target.value))}
          />
        </div>
      </div>

      <div className="row" style={{ marginTop: "1rem" }}>
        {language !== "en" && (
          <label className="check">
            <input
              type="checkbox"
              checked={includeEnglish}
              onChange={(event) => setIncludeEnglish(event.target.checked)}
            />
            İngilizce içeriği de dahil et
          </label>
        )}
        <label className="check">
          <input
            type="checkbox"
            checked={enableAsr}
            onChange={(event) => setEnableAsr(event.target.checked)}
          />
          Sesten transkript çıkar (yavaş)
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={enableStudyNotes}
            onChange={(event) => setEnableStudyNotes(event.target.checked)}
          />
          Çalışma notu üret (ek LLM çağrısı)
        </label>
      </div>

      {capabilities && !capabilities.youtube_search_configured && (
        <p className="alert" style={{ marginTop: "0.9rem" }}>
          YouTube Data API anahtarı tanımlı değil — arama yt-dlp ile yapılacak ve hız
          sınırlarına takılabilir.
        </p>
      )}

      {/* Kalan hak yalnizca sunucuda sinir varsa (`null` degilse) gosteriliyor.
          Hak bittiginde gonder dugmesi de kapaniyor: sunucu zaten 429 donecek,
          onu tiklamadan once soylemek daha durust. */}
      {capabilities?.runs_remaining_today !== null &&
        capabilities?.runs_remaining_today !== undefined && (
          <p
            className={capabilities.runs_remaining_today === 0 ? "alert alert--error" : "muted"}
            style={{ marginTop: "0.9rem" }}
          >
            {capabilities.runs_remaining_today === 0
              ? "Bugünlük çalıştırma hakkınız doldu. Yarın tekrar deneyebilirsiniz."
              : `Bugün kalan çalıştırma hakkınız: ${capabilities.runs_remaining_today}`}
          </p>
        )}

      <div style={{ marginTop: "1rem" }}>
        <button type="submit" disabled={busy || !topic.trim() || outOfRuns}>
          {busy ? "Oluşturuluyor…" : "Playlist Oluştur"}
        </button>
      </div>
    </form>
  );
}
