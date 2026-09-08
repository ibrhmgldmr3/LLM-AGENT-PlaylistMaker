/**
 * "Bu dersi izledim" isaretleri.
 *
 * Sunucuda ilerleme diye bir kavram YOK ve bunun icin bir uc eklemek arayuz
 * calismasinin kapsamini asardi. Ama isaretleyememek, sirali bir ders planini
 * elle takip etmeye zorluyor: kullanici nerede kaldigini hatirlamak zorunda
 * kaliyor. Tarayicida tutuluyor -- cihaza bagli, kaybolabilir, ve arayuz bunu
 * boyle ANLATIYOR (bkz. Derslerim ekranindaki not).
 *
 * Tek bir depo + abonelik: Derslerim listesi ile acik ders sayfasi AYNI veriyi
 * gosteriyor; ikisini ayri state'te tutmak, birinde isaretleyip digerine
 * dondugunde eski sayiyi gormek demekti.
 */

const DONE_KEY = "derslik:progress";
const TOTAL_KEY = "derslik:totals";

type DoneStore = Record<string, string[]>;
type TotalStore = Record<string, number>;

let doneCache: DoneStore | null = null;
let totalCache: TotalStore | null = null;
const listeners = new Set<() => void>();

function load<T extends object>(key: string): T {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as T;
  } catch {
    // Gizli sekme, dolu kota, bozuk JSON. Ilerleme ikincil bir kolaylik:
    // erisilemiyorsa uygulama calismaya DEVAM etmeli.
  }
  return {} as T;
}

function save(key: string, value: object) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* yazilamadi: oturum boyunca bellekte tutulmaya devam eder */
  }
  listeners.forEach((listener) => listener());
}

function doneStore(): DoneStore {
  if (!doneCache) doneCache = load<DoneStore>(DONE_KEY);
  return doneCache;
}

function totalStore(): TotalStore {
  if (!totalCache) totalCache = load<TotalStore>(TOTAL_KEY);
  return totalCache;
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const EMPTY: string[] = [];

/**
 * Ayni dizi ORNEGINI dondurmek sart: `useSyncExternalStore` her okumada yeni
 * bir dizi gorurse React sonsuz yeniden render dongusune girer.
 */
export function completedIds(runId: string): string[] {
  return doneStore()[runId] ?? EMPTY;
}

export function toggleCompleted(runId: string, videoId: string): void {
  const store = doneStore();
  const current = store[runId] ?? EMPTY;
  const next = current.includes(videoId)
    ? current.filter((id) => id !== videoId)
    : [...current, videoId];
  const updated = { ...store };
  if (next.length) updated[runId] = next;
  else delete updated[runId];
  doneCache = updated;
  save(DONE_KEY, updated);
}

/**
 * Bir dersin kac uniteden olustugu.
 *
 * Gecmis listesi yalnizca ozet aliyor; unite sayisi orada YOK. Ders bir kez
 * acildiginda ogrenilen sayi burada saklaniyor ki liste "3 / 8" gosterebilsin.
 * Hic acilmamis ders icin `null` doner ve liste ilerleme cubugu GOSTERMEZ --
 * bilmedigimiz bir seyi uydurmak yerine susuyoruz.
 */
export function rememberTotal(runId: string, total: number): void {
  if (total <= 0) return;
  const store = totalStore();
  if (store[runId] === total) return;
  const updated = { ...store, [runId]: total };
  totalCache = updated;
  save(TOTAL_KEY, updated);
}

export function knownTotal(runId: string): number | null {
  return totalStore()[runId] ?? null;
}

/** Ders silindiginde ilerlemesi de gitmeli; yoksa depo sonsuza kadar buyur. */
export function forgetRun(runId: string): void {
  const done = doneStore();
  const totals = totalStore();
  if (!(runId in done) && !(runId in totals)) return;
  const nextDone = { ...done };
  const nextTotals = { ...totals };
  delete nextDone[runId];
  delete nextTotals[runId];
  doneCache = nextDone;
  totalCache = nextTotals;
  save(DONE_KEY, nextDone);
  save(TOTAL_KEY, nextTotals);
}
