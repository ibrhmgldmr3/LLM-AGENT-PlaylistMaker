from __future__ import annotations

import os


# Mixed Conda environments that combine TensorFlow and whisper/ctranslate2 on Windows
# can crash on duplicate Intel OpenMP runtime initialization. Set the workaround as
# early as possible so imports later in the process inherit it.
if os.name == "nt":
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
