import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useLanguage, type Translate } from "../i18n";
import type {
  Citation,
  RagAnswer,
  RagRefusal,
  RunSummary,
  SourceTextResponse,
  SpaceDetail,
  SpaceSource,
} from "../api/types";
import { useJobStream } from "./useJobStream";

export interface RagMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  streaming: boolean;
  /**
   * Reddin turu; yanit verildiyse `null`.
   *
   * Mesajda TASINIYOR cunku arayuz reddi bir cevap balonu gibi cizemez:
   * "bu defterde yok" ile "kendi yanitimi dogrulayamadim" farkli seyler ve
   * ikisi de bir cevap DEGIL. Sunucu ayrimi `refusal` alaninda yapiyor;
   * burada onu mesajin omru boyunca sakliyoruz.
   */
  refusal: RagRefusal | null;
}

function messageId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Ham Markdown isaretlerini SOKER.
 *
 * Istem artik duz metin istiyor ama model bazen yine de Markdown yaziyor ve
 * yanit `white-space: pre-wrap` ile duz ciziliyor: ekranda `* **ÜFE:**` gibi
 * bir sey kaliyordu. Burada yapilan sey bicimlendirme UYDURMAK degil, okurun
 * gormemesi gereken SOZDIZIMINI kaldirmak; satir yapisi oldugu gibi kaliyor,
 * yani madde listeleri liste olarak okunmaya devam ediyor.
 */
export function plainProse(text: string): string {
  return text
    .replace(/```+/g, "")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^(\s*)[*+-]\s+/gm, "$1• ")
    .replace(/^\s*[-*_]{3,}\s*$/gm, "")
    .replace(/\*\*([^*\n]+)\*\*/g, "$1")
    .replace(/__([^_\n]+)__/g, "$1")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function answerText(answer: RagAnswer, t: Translate): string {
  if (answer.answered && answer.answer) return plainProse(answer.answer);
  // `reason` sunucudan KULLANICININ dilinde geliyor; yalnizca o yoksa
  // arayuzun kendi cumlesine dusuluyor.
  return answer.reason ?? t("rag.notFound", { n: answer.searched_sources });
}

/**
 * Reddin turu -- sunucu soylemediyse cikarilir.
 *
 * Geri duselim VAR cunku alan sonradan eklendi: alani bilmeyen bir sunucuyla
 * (ya da onbellekten donen eski bir yanitla) karsilasildiginda arayuz reddi
 * bir CEVAP gibi cizmemeli. Tur bilinmiyorsa en genel ret varsayiliyor.
 */
function refusalOf(answer: RagAnswer): RagRefusal | null {
  if (answer.answered) return null;
  return answer.refusal ?? "not_found";
}

export function useVideoRAG(spaceId: string | null) {
  const { t, answerLanguage } = useLanguage();

  const [space, setSpace] = useState<SpaceDetail | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [sourceText, setSourceText] = useState<SourceTextResponse | null>(null);
  const [sourceTextLoading, setSourceTextLoading] = useState(false);
  const [messages, setMessages] = useState<RagMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!spaceId) return;
    setLoading(true);
    setError(null);
    try {
      const [detail, runBody] = await Promise.all([api.getSpace(spaceId), api.listRuns(50, 0)]);
      setSpace(detail);
      setRuns(runBody.items.filter((run) => run.is_complete));
      setSelectedSourceId(
        (current) =>
          current ??
          detail.sources.find((source) => source.kind === "video")?.source_id ??
          detail.sources[0]?.source_id ??
          null,
      );
    } catch (exception) {
      setError(exception instanceof ApiError ? exception.message : t("error.spaceOpen"));
    } finally {
      setLoading(false);
    }
  }, [spaceId]);

  const loadSourceText = useCallback(
    async (sourceId: string) => {
      if (!spaceId || !sourceId) return;
      setSourceTextLoading(true);
      try {
        const textData = await api.getSourceText(spaceId, sourceId);
        setSourceText(textData);
      } catch {
        setSourceText(null);
      } finally {
        setSourceTextLoading(false);
      }
    },
    [spaceId],
  );

  const ingestJob = useJobStream(() => {
    void load();
    if (selectedSourceId) void loadSourceText(selectedSourceId);
  });

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (selectedSourceId) {
      void loadSourceText(selectedSourceId);
    } else {
      setSourceText(null);
    }
  }, [loadSourceText, selectedSourceId]);

  const addRun = useCallback(
    async (runId: string) => {
      if (!spaceId || !runId) return;
      setError(null);
      try {
        const accepted = await api.addRunToSpace(spaceId, runId);
        ingestJob.watch(accepted.job_id, accepted.events_url);
      } catch (exception) {
        setError(exception instanceof ApiError ? exception.message : t("error.runAdd"));
      }
    },
    [ingestJob, spaceId],
  );

  const uploadDocument = useCallback(
    async (file: File) => {
      if (!spaceId) return;
      setError(null);
      try {
        const accepted = await api.uploadDocument(spaceId, file);
        await load();
        ingestJob.watch(accepted.job_id, accepted.events_url);
      } catch (exception) {
        setError(exception instanceof ApiError ? exception.message : t("error.docUpload"));
      }
    },
    [ingestJob, load, spaceId],
  );

  const transcribeSource = useCallback(
    async (sourceId: string) => {
      if (!spaceId || !sourceId) return;
      setError(null);
      try {
        const accepted = await api.transcribeSource(spaceId, sourceId);
        await load();
        ingestJob.watch(accepted.job_id, accepted.events_url);
      } catch (exception) {
        setError(exception instanceof ApiError ? exception.message : t("error.transcribe"));
      }
    },
    [ingestJob, load, spaceId],
  );

  const deleteSource = useCallback(
    async (sourceId: string) => {
      if (!spaceId) return;
      setError(null);
      try {
        await api.deleteSource(spaceId, sourceId);
        await load();
      } catch (exception) {
        setError(exception instanceof ApiError ? exception.message : t("error.sourceRemove"));
      }
    },
    [load, spaceId],
  );

  const ask = useCallback(
    async (question: string) => {
      if (!spaceId || asking) return;
      const trimmed = question.trim();
      if (!trimmed) return;

      const assistantId = messageId();
      setAsking(true);
      setError(null);
      setMessages((items) => [
        ...items,
        {
          id: messageId(),
          role: "user",
          content: trimmed,
          citations: [],
          streaming: false,
          refusal: null,
        },
        {
          id: assistantId,
          role: "assistant",
          content: "",
          citations: [],
          streaming: true,
          refusal: null,
        },
      ]);

      try {
        const answer = await api.ask(spaceId, trimmed, answerLanguage);
        const text = answerText(answer, t);
        const refusal = refusalOf(answer);

        // Reddedilen yanit YAZILMIYOR, aninda konuyor. Harf harf akan bir
        // "bulamadim" cumlesi, sistemin cevabi dusundugunu ima ederdi; oysa
        // burada dusunulecek bir sey yok -- karar zaten verilmis.
        if (refusal) {
          setMessages((items) =>
            items.map((item) =>
              item.id === assistantId
                ? { ...item, content: text, citations: [], streaming: false, refusal }
                : item,
            ),
          );
          return;
        }

        for (let index = 0; index <= text.length; index += 10) {
          const partial = text.slice(0, index);
          setMessages((items) =>
            items.map((item) =>
              item.id === assistantId
                ? { ...item, content: partial, citations: index >= text.length ? answer.citations : item.citations }
                : item,
            ),
          );
          await new Promise((resolve) => window.setTimeout(resolve, 12));
        }
        setMessages((items) =>
          items.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  content: text,
                  citations: answer.citations,
                  streaming: false,
                  refusal: null,
                }
              : item,
          ),
        );
      } catch (exception) {
        setMessages((items) => items.filter((item) => item.id !== assistantId));
        setError(exception instanceof ApiError ? exception.message : t("error.answer"));
      } finally {
        setAsking(false);
      }
    },
    [asking, spaceId],
  );

  const selectedSource: SpaceSource | null =
    space?.sources.find((source) => source.source_id === selectedSourceId) ?? space?.sources[0] ?? null;

  return {
    space,
    runs,
    selectedSource,
    selectedSourceId,
    sourceText,
    sourceTextLoading,
    messages,
    loading,
    asking,
    error,
    ingestJob,
    reload: load,
    setSelectedSourceId,
    loadSourceText,
    addRun,
    uploadDocument,
    transcribeSource,
    deleteSource,
    ask,
  };
}

