import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, ListVideo } from "lucide-react";
import { api, ApiError } from "../../api/client";
import type { YoutubeAuthStatus } from "../../api/client";
import { Note, Spinner } from "../../components/ui";

/**
 * YouTube hesabini baglar ve tamamlanmis bir ders planini playlist olarak
 * yayinlar.
 *
 * Yayinlama planin URETIMINDEN ayri: yetkilendirme sonradan yapilip tekrar
 * denenebilir, bir hata ders planini kaybetmez.
 */
export function PublishPanel({ runId, publishedUrl }: { runId: string; publishedUrl: string | null }) {
  const [status, setStatus] = useState<YoutubeAuthStatus | null>(null);
  const [url, setUrl] = useState<string | null>(publishedUrl);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.youtubeStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    refresh();
    // Google'dan donuste sonucu bildir ve adres cubugunu temizle.
    const params = new URLSearchParams(window.location.search);
    const result = params.get("youtube_auth");
    if (result) {
      setNotice(result === "ok" ? "YouTube hesabın bağlandı." : "Yetkilendirme iptal edildi.");
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [refresh]);

  useEffect(() => setUrl(publishedUrl), [publishedUrl, runId]);

  const connect = () => {
    setError(null);
    api
      .youtubeAuthUrl()
      .then(({ authorization_url }) => {
        window.location.href = authorization_url;
      })
      .catch((exception: ApiError) => setError(exception.message));
  };

  const publish = () => {
    setBusy(true);
    setError(null);
    api
      .publish(runId)
      .then((response) => {
        setUrl(response.url);
        setNotice(`${response.added} video eklendi.`);
      })
      .catch((exception: ApiError) => setError(exception.message))
      .finally(() => setBusy(false));
  };

  const disconnect = () => {
    api.youtubeDisconnect().then(refresh).catch(() => refresh());
  };

  // Kurulum eksikse bu bir KULLANICI hatasi degil, yoneticinin isi. Sessizce
  // gizlemek yerine tek satirlik notrbir aciklama: kullanici dugmeyi arayip
  // bulamamaktansa neden olmadigini bilsin.
  if (!status?.configured) {
    return (
      <p className="hint" style={{ marginTop: 0 }}>
        YouTube'a yayınlama bu kurulumda kapalı (<code>YOUTUBE_OAUTH_CLIENT_ID</code> ve{" "}
        <code>YOUTUBE_OAUTH_CLIENT_SECRET</code> tanımlı değil).
      </p>
    );
  }

  return (
    <div className="stack stack--tight">
      {notice && <Note tone="ok" role="status">{notice}</Note>}
      {error && (
        <Note tone="danger" role="alert" title="Yayınlanamadı">
          {error}
        </Note>
      )}

      {url ? (
        <div className="row">
          <ListVideo className="h-4 w-4 text-[color:var(--ink-3)]" aria-hidden />
          <span className="meta">Bu plan YouTube'da bir playlist olarak duruyor.</span>
          <a className="link-out" href={url} target="_blank" rel="noreferrer">
            Playlist'i aç
            <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
          </a>
        </div>
      ) : status.connected ? (
        <div className="row">
          <button type="button" className="btn" onClick={publish} disabled={busy}>
            {busy ? <Spinner /> : <ListVideo className="h-4 w-4" aria-hidden />}
            {busy ? "Yayınlanıyor…" : "Playlist olarak yayınla"}
          </button>
          <button type="button" className="btn btn--quiet" onClick={disconnect}>
            Hesabı ayır
          </button>
          <span className="meta">Planı YouTube hesabına kaydeder, telefonda da izlersin.</span>
        </div>
      ) : (
        <div className="row">
          <button type="button" className="btn" onClick={connect}>
            <ListVideo className="h-4 w-4" aria-hidden />
            YouTube hesabını bağla
          </button>
          <span className="meta">Yayınlamak için bir kez yetkilendirme gerekir.</span>
        </div>
      )}
    </div>
  );
}
