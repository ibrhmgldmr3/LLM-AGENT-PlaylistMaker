import { useId, useState } from "react";
import { ChevronDown, Play, SlidersHorizontal } from "lucide-react";
import type { Capabilities, CreateRunRequest, Difficulty, Freshness, Language } from "../../api/types";
import { Note, Spinner } from "../../components/ui";

interface Props {
  capabilities: Capabilities | null;
  busy: boolean;
  onSubmit: (payload: CreateRunRequest) => void;
}

/**
 * Bos bir alana bakip ne yazacagini bilememek, bu ekranin en pahali anı.
 * Ornekler SUSLEME DEGIL: tiklanabilir ve alanı dolduruyorlar, yani "ne kadar
 * dar/genis yazmaliyim" sorusunu gostererek yanitliyorlar.
 */
const EXAMPLES = [
  "Doğrusal cebirde özdeğerler ve özvektörler",
  "React'te durum yönetimi",
  "Makroekonomide enflasyon nasıl ölçülür",
  "Fotoğrafta ışık ölçümü",
];

function Check({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint: string;
}) {
  const id = useId();
  return (
    <div className="check">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        aria-describedby={`${id}-hint`}
        onChange={(event) => onChange(event.target.checked)}
      />
      <label className="check__text" htmlFor={id}>
        {label}
      </label>
      <p className="check__hint" id={`${id}-hint`}>
        {hint}
      </p>
    </div>
  );
}

export function RunForm({ capabilities, busy, onSubmit }: Props) {
  const outOfRuns = capabilities?.runs_remaining_today === 0;
  // Ortak kapasite kullanicinin kendi hakkindan BAGIMSIZ: hakki olsa bile
  // servisin gunluk kota butcesi bittiyse calistirma baslatilamiyor.
  const serviceFull = capabilities?.service_capacity_reached === true;
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

  const blocked = busy || !topic.trim() || outOfRuns || serviceFull;

  return (
    <form className="start" onSubmit={submit}>
      <div className="stack stack--tight">
        <h2 className="start__q">
          <label htmlFor="topic">Ne öğrenmek istiyorsun?</label>
        </h2>
        <p className="start__sub">
          Tek bir hedef yaz. Konuyu alt başlıklara ayırıp her biri için en uygun videoyu
          seçiyoruz; sonunda baştan sona izlenecek sıralı bir ders planın oluyor.
        </p>
      </div>

      <div className="start__ask">
        <input
          id="topic"
          type="text"
          value={topic}
          autoComplete="off"
          placeholder="Örnek: Makine öğrenmesiyle zaman serisi tahmini"
          onChange={(event) => setTopic(event.target.value)}
        />
        <button type="submit" className="btn btn--primary btn--lg" disabled={blocked}>
          {busy ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}
          {busy ? "Oluşturuluyor…" : "Ders planımı oluştur"}
        </button>
      </div>

      <div className="start__examples">
        <span className="start__examples-label">Şunları deneyebilirsin:</span>
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            className="chip"
            disabled={busy}
            onClick={() => setTopic(example)}
          >
            {example}
          </button>
        ))}
      </div>

      {serviceFull ? (
        <Note tone="danger" title="Bugünlük kapasite doldu">
          Servisin bugünkü kapasitesi doldu. Arama kotası tüm kullanıcılar için ortak; kota
          sıfırlandığında (Pasifik saatiyle gece yarısı) yeniden deneyebilirsin.
        </Note>
      ) : (
        /* Kalan hak yalnizca sunucuda sinir varsa (`null` degilse) gosteriliyor.
           Hak bittiginde gonder dugmesi de kapaniyor: sunucu zaten 429 donecek,
           onu tiklamadan once soylemek daha durust. */
        capabilities?.runs_remaining_today !== null &&
        capabilities?.runs_remaining_today !== undefined &&
        (capabilities.runs_remaining_today === 0 ? (
          <Note tone="danger" title="Bugünlük hakkın doldu">
            Yarın yeniden ders planı oluşturabilirsin. Bu arada “Derslerim”deki planlarına
            çalışmaya devam edebilirsin.
          </Note>
        ) : (
          <p className="meta">
            Bugün {capabilities.runs_remaining_today} ders planı hakkın kaldı.
          </p>
        ))
      )}

      {capabilities && !capabilities.youtube_search_configured && (
        <Note tone="warn" title="Arama yedek yöntemle yapılacak">
          YouTube Data API anahtarı tanımlı değil — arama yt-dlp ile yapılacak ve hız
          sınırlarına takılabilir.
        </Note>
      )}

      <details className="prefs">
        <summary>
          <SlidersHorizontal className="h-4 w-4" aria-hidden />
          Tercihler
          <span className="meta" style={{ fontWeight: 400 }}>
            Dil · Seviye · Süre
          </span>
          <ChevronDown className="prefs__caret h-4 w-4" aria-hidden />
        </summary>

        <div className="prefs__body">
          <div className="prefs__grid">
            <div>
              <label className="label" htmlFor="language">
                Dil
              </label>
              <select
                id="language"
                value={language}
                onChange={(event) => setLanguage(event.target.value as Language)}
              >
                <option value="tr">Türkçe</option>
                <option value="en">English</option>
              </select>
              <p className="hint">Videoların ve çalışma notlarının dili.</p>
            </div>

            <div>
              <label className="label" htmlFor="difficulty">
                Seviye
              </label>
              <select
                id="difficulty"
                value={difficulty}
                onChange={(event) => setDifficulty(event.target.value as Difficulty)}
              >
                <option value="mixed">Karışık</option>
                <option value="beginner">Yeni başlıyorum</option>
                <option value="intermediate">Temeli var</option>
                <option value="advanced">İleri düzey</option>
              </select>
              <p className="hint">Konuya ne kadar yakın olduğunu söyler.</p>
            </div>

            <div>
              <label className="label" htmlFor="freshness">
                Video yaşı
              </label>
              <select
                id="freshness"
                value={freshness}
                onChange={(event) => setFreshness(event.target.value as Freshness)}
              >
                <option value="balanced">Fark etmez</option>
                <option value="evergreen">Zamansız anlatımlar</option>
                <option value="recent">Yeni videolar</option>
              </select>
              <p className="hint">Hızlı değişen konularda “yeni” işe yarar.</p>
            </div>

            <div>
              <label className="label" htmlFor="duration">
                <span>Ders başına en fazla süre: {maxDuration} dk</span>
              </label>
              <input
                id="duration"
                type="range"
                min={10}
                max={180}
                step={5}
                value={maxDuration}
                onChange={(event) => setMaxDuration(Number(event.target.value))}
              />
              <p className="hint">Kısa tutarsan daha derli toplu, uzun tutarsan daha derin.</p>
            </div>
          </div>

          <div className="prefs__opts">
            {language !== "en" && (
              <Check
                checked={includeEnglish}
                onChange={setIncludeEnglish}
                label="İngilizce videoları da değerlendir"
                hint="Türkçe kaynak az olan konularda seçenekleri genişletir."
              />
            )}
            <Check
              checked={enableAsr}
              onChange={setEnableAsr}
              label="Altyazısı olmayan videoların sesini yazıya dök"
              hint="Daha iyi eşleşme sağlar ama hazırlık belirgin şekilde uzar."
            />
            <Check
              checked={enableStudyNotes}
              onChange={setEnableStudyNotes}
              label="Her ders için çalışma notu çıkar"
              hint="Videonun transkriptinden kısa bir özet üretilir."
            />
          </div>
        </div>
      </details>
    </form>
  );
}
