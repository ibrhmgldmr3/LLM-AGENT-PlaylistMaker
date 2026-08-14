import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { YoutubeAuthStatus } from "../../api/client";

/**
 * YouTube hesabini baglar ve tamamlanmis bir calistirmayi playlist olarak yayinlar.
 *
 * Yayinlama build'den AYRI: yetkilendirme sonradan yapilip tekrar denenebilir,
 * bir hata playlist uretimini kaybetmez.
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
      setNotice(result === "ok" ? "YouTube hesabı bağlandı." : "Yetkilendirme iptal edildi.");
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

  if (!status?.configured) {
    return (
      <div className="card">
        <h3>YouTube'a yayınla</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          OAuth istemci bilgileri tanımlı değil. <code>YOUTUBE_OAUTH_CLIENT_ID</code> ve{" "}
          <code>YOUTUBE_OAUTH_CLIENT_SECRET</code> ayarlayın.
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>YouTube'a yayınla</h3>
      {notice && <p className="alert">{notice}</p>}
      {error && <p className="alert alert--error">{error}</p>}

      {url ? (
        <p style={{ marginBottom: 0 }}>
          Playlist oluşturuldu:{" "}
          <a href={url} target="_blank" rel="noreferrer">
            YouTube'da aç ↗
          </a>
        </p>
      ) : status.connected ? (
        <div className="row">
          <button onClick={publish} disabled={busy}>
            {busy ? "Yayınlanıyor…" : "Playlist olarak yayınla"}
          </button>
          <button className="ghost" onClick={disconnect}>
            Hesabı ayır
          </button>
        </div>
      ) : (
        <div className="row">
          <button onClick={connect}>YouTube hesabını bağla</button>
          <span className="muted">Yayınlamak için bir kez yetkilendirme gerekir.</span>
        </div>
      )}
    </div>
  );
}
