import { useCallback, useEffect, useState } from "react";
import { BookOpen, ChevronLeft, ChevronRight, GraduationCap, Plus, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import type { RunSummary } from "../../api/types";
import { Empty, Meter, Note } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { forgetRun } from "../../lib/progress";
import { useRunProgress } from "../../hooks/useProgress";
import { useLanguage, useT } from "../../i18n";

const PAGE_SIZE = 10;

function CourseRow({
  item,
  onOpen,
  onDelete,
}: {
  item: RunSummary;
  onOpen: (runId: string) => void;
  onDelete: (runId: string) => void;
}) {
  const { t, lang } = useLanguage();
  const progress = useRunProgress(item.run_id);
  const finished = progress.total !== null && progress.done >= progress.total;

  return (
    <article className="course-row">
      <span className="course-row__mark" aria-hidden>
        {finished ? <GraduationCap className="h-4 w-4" /> : <BookOpen className="h-4 w-4" />}
      </span>

      <div className="course-row__body min-w-0">
        <h3 className="course-row__title">{item.topic}</h3>
        <p className="meta" style={{ marginTop: "0.2rem" }}>
          {formatDateTime(item.created_at, lang)}
          {!item.is_complete && t("history.unfinished")}
        </p>
      </div>

      {item.is_complete && progress.total !== null && (
        <div className="course-row__prog">
          <div className="row row--between" style={{ gap: "0.5rem", marginBottom: "0.35rem" }}>
            <span className="meta">{finished ? t("history.done") : t("history.progress")}</span>
            <span className="meta" style={{ fontWeight: 600 }}>
              {progress.done} / {progress.total}
            </span>
          </div>
          <Meter
            value={progress.done}
            max={progress.total}
            variant={finished ? undefined : "signal"}
            label={t("history.meterLabel", { topic: item.topic, total: progress.total, done: progress.done })}
          />
        </div>
      )}

      <div className="row" style={{ gap: "0.4rem" }}>
        {item.is_complete && (
          <button type="button" className="btn" onClick={() => onOpen(item.run_id)}>
            {t("history.open")}
          </button>
        )}
        <button
          type="button"
          className="btn btn--quiet btn--danger"
          onClick={() => onDelete(item.run_id)}
        >
          <Trash2 className="h-4 w-4" aria-hidden />
          {t("history.delete")}
        </button>
      </div>
    </article>
  );
}

export function HistoryList({
  onOpen,
  onCreate,
}: {
  onOpen: (runId: string) => void;
  onCreate?: () => void;
}) {
  const t = useT();
  const [items, setItems] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((nextOffset: number) => {
    api
      .listRuns(PAGE_SIZE, nextOffset)
      .then((body) => {
        // Bos sayfa + sifirdan buyuk offset = sayfanin son ogesi silinmis.
        // Eskiden burada durulup "Henuz calistirma yok" gosteriliyordu; ayni
        // kosul sayfalama dugmelerini de gizledigi icin onceki sayfadaki
        // calistirmalara donus yolu KALMIYORDU. Bir sayfa geri kayip tekrar dene.
        if (body.items.length === 0 && nextOffset > 0) {
          load(Math.max(0, nextOffset - PAGE_SIZE));
          return;
        }
        setItems(body.items);
        setTotal(body.total);
        setOffset(body.offset);
        setError(null);
      })
      .catch((exception: Error) => setError(exception.message));
  }, []);

  useEffect(() => load(0), [load]);

  const remove = (runId: string) => {
    api
      .deleteRun(runId)
      .then(() => {
        // Ders gidince ilerlemesi de gitmeli; yoksa tarayicidaki depo
        // silinmis derslerin kayitlariyla sonsuza kadar buyur.
        forgetRun(runId);
        load(offset);
      })
      .catch((exception: Error) => setError(exception.message));
  };

  if (error) {
    return (
      <Note tone="danger" role="alert" title={t("history.loadFailed")}>
        {error}
      </Note>
    );
  }

  if (items.length === 0) {
    return (
      <Empty
        icon={BookOpen}
        title={t("history.emptyTitle")}
        action={
          onCreate && (
            <button type="button" className="btn btn--primary" onClick={onCreate}>
              <Plus className="h-4 w-4" aria-hidden />
              {t("history.emptyAction")}
            </button>
          )
        }
      >
        {t("history.emptyBody")}
      </Empty>
    );
  }

  return (
    <div className="stack stack--loose">
      <div className="courses">
        {items.map((item) => (
          <CourseRow key={item.run_id} item={item} onOpen={onOpen} onDelete={remove} />
        ))}
      </div>

      <p className="hint" style={{ marginTop: 0 }}>
        {t("history.localNote")}
      </p>

      {total > PAGE_SIZE && (
        <div className="pager">
          <button
            type="button"
            className="btn btn--quiet"
            disabled={offset === 0}
            onClick={() => load(offset - PAGE_SIZE)}
          >
            <ChevronLeft className="h-4 w-4" aria-hidden />
            {t("history.prev")}
          </button>
          <span className="meta">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} / {total}
          </span>
          <button
            type="button"
            className="btn btn--quiet"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => load(offset + PAGE_SIZE)}
          >
            {t("history.next")}
            <ChevronRight className="h-4 w-4" aria-hidden />
          </button>
        </div>
      )}
    </div>
  );
}
