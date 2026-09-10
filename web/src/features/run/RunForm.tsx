import { useId, useState } from "react";
import { ChevronDown, Play, SlidersHorizontal } from "lucide-react";
import type { Capabilities, CreateRunRequest, Difficulty, Freshness, Language } from "../../api/types";
import { Note, Spinner } from "../../components/ui";
import { useT } from "../../i18n";
import type { Dict } from "../../i18n/dict";

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
/**
 * Gonderdikten sonraki guzergah. YALNIZCA her calistirmada gerceklesen uc
 * asama: transkript okuma ve calisma notu tercihe bagli, onlari da saymak
 * bos bir ekranda tutulmayabilecek bir soz vermek olurdu.
 */
const ROUTE: (keyof Dict)[] = ["start.route1", "start.route2", "start.route3"];

const EXAMPLES: (keyof Dict)[] = [
  "start.example1",
  "start.example2",
  "start.example3",
  "start.example4",
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
  const t = useT();
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
          <label htmlFor="topic">{t("start.q")}</label>
        </h2>
        <p className="start__sub">{t("start.sub")}</p>
      </div>

      <div className="start__ask">
        <input
          id="topic"
          type="text"
          value={topic}
          autoComplete="off"
          placeholder={t("start.placeholder")}
          onChange={(event) => setTopic(event.target.value)}
        />
        <button type="submit" className="btn btn--primary btn--lg" disabled={blocked}>
          {busy ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}
          {busy ? t("start.submitting") : t("start.submit")}
        </button>
      </div>

      <div className="start__examples">
        <span className="start__examples-label">{t("start.examplesLabel")}</span>
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            className="chip"
            disabled={busy}
            onClick={() => setTopic(t(example))}
          >
            {t(example)}
          </button>
        ))}
      </div>

      {serviceFull ? (
        <Note tone="danger" title={t("start.capacityTitle")}>
          {t("start.capacityBody")}
        </Note>
      ) : (
        /* Kalan hak yalnizca sunucuda sinir varsa (`null` degilse) gosteriliyor.
           Hak bittiginde gonder dugmesi de kapaniyor: sunucu zaten 429 donecek,
           onu tiklamadan once soylemek daha durust. */
        capabilities?.runs_remaining_today !== null &&
        capabilities?.runs_remaining_today !== undefined &&
        (capabilities.runs_remaining_today === 0 ? (
          <Note tone="danger" title={t("start.quotaTitle")}>
            {t("start.quotaBody")}
          </Note>
        ) : (
          <p className="meta start__quota">
            {t("start.quotaLeft", { n: capabilities.runs_remaining_today })}
          </p>
        ))
      )}

      {capabilities && !capabilities.youtube_search_configured && (
        <Note tone="warn" title={t("start.fallbackTitle")}>
          {t("start.fallbackBody")}
        </Note>
      )}

      <details className="prefs">
        <summary>
          <SlidersHorizontal className="h-4 w-4" aria-hidden />
          {t("prefs.summary")}
          <span className="meta" style={{ fontWeight: 400 }}>
            {t("prefs.summaryHint")}
          </span>
          <ChevronDown className="prefs__caret h-4 w-4" aria-hidden />
        </summary>

        <div className="prefs__body">
          <div className="prefs__grid">
            <div>
              <label className="label" htmlFor="language">
                {t("prefs.language")}
              </label>
              <select
                id="language"
                value={language}
                onChange={(event) => setLanguage(event.target.value as Language)}
              >
                <option value="tr">{t("shell.languageTr")}</option>
                <option value="en">{t("shell.languageEn")}</option>
              </select>
              <p className="hint">{t("prefs.languageHint")}</p>
            </div>

            <div>
              <label className="label" htmlFor="difficulty">
                {t("prefs.level")}
              </label>
              <select
                id="difficulty"
                value={difficulty}
                onChange={(event) => setDifficulty(event.target.value as Difficulty)}
              >
                <option value="mixed">{t("prefs.level.mixed")}</option>
                <option value="beginner">{t("prefs.level.beginner")}</option>
                <option value="intermediate">{t("prefs.level.intermediate")}</option>
                <option value="advanced">{t("prefs.level.advanced")}</option>
              </select>
              <p className="hint">{t("prefs.levelHint")}</p>
            </div>

            <div>
              <label className="label" htmlFor="freshness">
                {t("prefs.age")}
              </label>
              <select
                id="freshness"
                value={freshness}
                onChange={(event) => setFreshness(event.target.value as Freshness)}
              >
                <option value="balanced">{t("prefs.age.balanced")}</option>
                <option value="evergreen">{t("prefs.age.evergreen")}</option>
                <option value="recent">{t("prefs.age.recent")}</option>
              </select>
              <p className="hint">{t("prefs.ageHint")}</p>
            </div>

            <div>
              <label className="label" htmlFor="duration">
                <span>{t("prefs.duration", { n: maxDuration })}</span>
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
              <p className="hint">{t("prefs.durationHint")}</p>
            </div>
          </div>

          <div className="prefs__opts">
            {language !== "en" && (
              <Check
                checked={includeEnglish}
                onChange={setIncludeEnglish}
                label={t("prefs.english")}
                hint={t("prefs.englishHint")}
              />
            )}
            <Check
              checked={enableAsr}
              onChange={setEnableAsr}
              label={t("prefs.asr")}
              hint={t("prefs.asrHint")}
            />
            <Check
              checked={enableStudyNotes}
              onChange={setEnableStudyNotes}
              label={t("prefs.notes")}
              hint={t("prefs.notesHint")}
            />
          </div>
        </div>
      </details>

      {/* Ilerleme panelindeki AYNI yol cihazi, yatay yatirilmis. Gonderildigi
          anda bu serit kalkiyor ve asagida canlisi aciliyor -- onizleme yerini
          gercege birakiyor.

          Bos baslangic ekraninin altindaki gri bant icin dogru olan tek sey
          buydu: salon rengi anahtari ekranda hic kaynak yokken ogretilemez,
          ogretilseydi kullanici ilk gercek kaynakta yanlis cikacak bir sey
          ezberlerdi. */}
      {!busy && (
        <div className="start__route">
          <span className="start__route-label" id="route-preview-label">
            {t("start.routeLabel")}
          </span>
          <ol className="route route--preview" aria-labelledby="route-preview-label">
            {ROUTE.map((step, index) => (
              <li className="route__step" key={step}>
                <span className="route__dot" aria-hidden>
                  {index + 1}
                </span>
                <p className="route__label">{t(step)}</p>
              </li>
            ))}
          </ol>
        </div>
      )}
    </form>
  );
}
