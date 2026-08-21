from __future__ import annotations

# Burada BILEREK `KMP_DUPLICATE_LIB_OK` ayarlanmiyor.
#
# Karisik Conda ortamlarinda (TensorFlow + ctranslate2) Windows'ta cift Intel
# OpenMP calisma zamani cokmeye yol aciyor ve bayrak bunu asiyor. Ama import
# zamaninda kurmak, `ALLOW_UNSAFE_OPENMP_WORKAROUND=false` diyen kullanicinin
# secimini SESSIZCE eziyordu: bu modul config okunmadan once yukleniyor ve
# `setdefault` sonraki (config'e bakan) cagrilari da etkisiz birakiyordu --
# ayar README'de duruyor ama hicbir sey yapmiyordu.
#
# Bayrak artik yalnizca gercekten gerektigi iki yerde, config'e BAKARAK
# kuruluyor: `build_playlist` girisinde ve `FasterWhisperProvider.transcribe`
# icinde. Ikisi de `faster_whisper` import edilmeden once calisiyor (import
# `_load_model` icinde tembel), yani erkenlik korunuyor.
