/**
 * Kaynaga SALON RENGI atar (1..6).
 *
 * Renk bu dunyada sus degil ANAHTAR: bir kaynagin rengi kunyesinde, kaynak
 * listesinde ve transkript isaretinde AYNI olmali ki kullanici rengi bir kez
 * ogrenip sonra okumadan tanisin. Bu yuzden renk sirayla dagitilmiyor --
 * siralama degisince renk de degisirdi -- kaynagin KIMLIGINDEN turetiliyor.
 *
 * Deterministik ve durum tutmuyor: ayni `source_id` her oturumda, her bilesende
 * ayni rengi alir. Cakisma (iki kaynagin ayni rengi almasi) kabul ediliyor:
 * alti renk var ve bir defterde daha fazla kaynak olabilir. Renk burada bir
 * kimlik dogrulamasi degil bir hatirlatici; cakisma okunabilirligi bozmuyor.
 *
 * Kaynaklarin yaninda DEFTERLERE de uygulaniyor (`space_id`): defter listesinde
 * her satir kendi salonunun rengini tasiyor. Ayni islev, ayni kural -- kimlik
 * ne olursa olsun renk ondan turuyor.
 */
const ROOM_COUNT = 6;

export function roomKey(id: string): number {
  let hash = 0;
  for (let index = 0; index < id.length; index += 1) {
    // eslint-disable-next-line no-bitwise
    hash = (hash * 31 + id.charCodeAt(index)) | 0;
  }
  return (Math.abs(hash) % ROOM_COUNT) + 1;
}

/** `style` icin hazir ozel ozellik: `--key` degiskenini o salonun rengine baglar. */
export function roomStyle(id: string): React.CSSProperties {
  return { ["--key" as string]: `var(--key-${roomKey(id)})` } as React.CSSProperties;
}
