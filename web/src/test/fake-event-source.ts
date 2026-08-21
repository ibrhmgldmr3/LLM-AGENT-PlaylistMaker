/**
 * Testler icin kontrol edilebilir `EventSource`.
 *
 * jsdom `EventSource` saglamiyor, dolayisiyla bir sahte zaten sart. Ama asil
 * sebep bu degil: gercek olani kullanabilseydik bile testin olaylari ISTEDIGI
 * ANDA gonderebilmesi gerekir -- `progress` sonra `done`, ya da govdesiz bir
 * `error`. Sahte olan `emit()` ile bunu deterministik yapiyor; zamanlamaya
 * dayali bekleme yok.
 *
 * `close()` cagrilip cagrilmadigini da kaydediyor: hook'un akisi sizdirmadigi
 * (yeniden izlemede ve unmount'ta kapattigi) yalnizca boyle dogrulanabiliyor.
 */
export class FakeEventSource {
  /** Kurulan tum ornekler, kurulus sirasiyla. */
  static instances: FakeEventSource[] = [];

  static reset(): void {
    FakeEventSource.instances = [];
  }

  /** En son kurulan ornek; cogu test bununla ilgileniyor. */
  static get last(): FakeEventSource {
    const source = FakeEventSource.instances.at(-1);
    if (!source) throw new Error("Hic EventSource kurulmadi");
    return source;
  }

  /** `EventSource.CLOSED`. Gercegiyle ayni sayi olmali. */
  static readonly CLOSED = 2;

  readonly url: string;
  closed = false;

  /**
   * Gercek `EventSource`in durum makinesi: 0 CONNECTING, 1 OPEN, 2 CLOSED.
   *
   * Ayrim onemli: gecici bir kopmada tarayici CONNECTING'e donup kendisi
   * yeniden dener; sunucu 200 disi yanit verirse (ornegin oturum dustu ve
   * 401 geldi) baglantiyi KALICI olarak kapatir ve bir daha denemez. Ikisi de
   * govdesiz bir `error` olayi yayiyor, tek fark bu alan.
   */
  readyState = 1;

  private readonly listeners = new Map<string, Set<(event: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    const existing = this.listeners.get(type) ?? new Set();
    existing.add(listener);
    this.listeners.set(type, existing);
  }

  removeEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }

  /** Sunucunun 200 disi yanit vermesi: kalici kapanma, yeniden deneme YOK. */
  failPermanently(): void {
    this.readyState = FakeEventSource.CLOSED;
    for (const listener of this.listeners.get("error") ?? []) {
      listener(new Event("error") as MessageEvent);
    }
  }

  /**
   * Sunucudan bir olay gelmis gibi davranir.
   *
   * `data` verilmezse GOVDESIZ olay uretilir. Bu ayrim onemli: tarayici baglanti
   * koptugunda `data` alani hic olmayan duz bir `Event` gonderiyor, sunucunun
   * yolladigi `event: error` ise govdeli bir `MessageEvent`. Hook ikisini
   * birbirinden govdenin varligiyla ayirt ediyor, o yuzden sahte de ayni sekli
   * uretmek zorunda -- govdesiz durumda `MessageEvent` uretmek testi gercekte
   * olmayan bir sey uzerinde dogrulamak olurdu.
   *
   * Kapatilmis akis HICBIR SEY yaymaz. Gercek `EventSource` de boyle: `close()`
   * sonrasi olay teslim edilmiyor. Sahte bunu taklit etmezse, gerceklesemeyecek
   * bir senaryo (kapali akistan gec gelen `done`) test edilmis olur.
   */
  emit(type: string, data?: unknown): void {
    if (this.closed) return;
    const event =
      data === undefined ? new Event(type) : new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event as MessageEvent);
  }
}

/** Sahteyi kurar ve sokmek icin bir fonksiyon dondurur. */
export function installFakeEventSource(): () => void {
  const original = (globalThis as { EventSource?: unknown }).EventSource;
  FakeEventSource.reset();
  (globalThis as { EventSource?: unknown }).EventSource = FakeEventSource;
  return () => {
    (globalThis as { EventSource?: unknown }).EventSource = original;
  };
}
