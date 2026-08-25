import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  Citation,
  RagAnswer,
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
}

function messageId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function answerText(answer: RagAnswer): string {
  if (answer.answered && answer.answer) return answer.answer;
  return (
    answer.reason ??
    `${answer.searched_sources} kaynak arandı ama bu soruyu karşılayan bir bölüm bulunamadı. Deftere ilgili bir video ya da döküman eklemeyi deneyebilirsin.`
  );
}

export function useVideoRAG(spaceId: string | null) {
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
      setError(exception instanceof ApiError ? exception.message : "Defter açılamadı.");
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
        setError(exception instanceof ApiError ? exception.message : "Ders planı eklenemedi.");
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
        setError(exception instanceof ApiError ? exception.message : "Döküman yüklenemedi.");
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
        setError(exception instanceof ApiError ? exception.message : "Transkript çıkarma başlatılamadı.");
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
        setError(exception instanceof ApiError ? exception.message : "Kaynak kaldırılamadı.");
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
        { id: messageId(), role: "user", content: trimmed, citations: [], streaming: false },
        { id: assistantId, role: "assistant", content: "", citations: [], streaming: true },
      ]);

      try {
        const answer = await api.ask(spaceId, trimmed);
        const text = answerText(answer);
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
              ? { ...item, content: text, citations: answer.citations, streaming: false }
              : item,
          ),
        );
      } catch (exception) {
        setMessages((items) => items.filter((item) => item.id !== assistantId));
        setError(exception instanceof ApiError ? exception.message : "Yanıt üretilemedi.");
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

