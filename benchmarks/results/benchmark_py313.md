Python 3.13.9 (CPython), GIL: włączony, rdzenie: 28, wątków/procesów: 8, system: Windows 11 (AMD64)

| Scenariusz | Dane | Sekwencyjnie | Wątki | Procesy | Przysp. wątki | Przysp. procesy |
|---|---|---:|---:|---:|---:|---:|
| CPU – czysty Python (FNV-1a) | 16 plików, 16.0 MiB | 0.87 s | 0.87 s | 0.28 s | 1.00× | 3.17× |
| CPU – hashlib SHA-256 | 16 plików, 256.0 MiB | 0.18 s | 0.03 s | 0.17 s | 5.92× | 1.05× |
| I/O – dysk sieciowy (symulacja) | 32 plików, 2.0 MiB | 1.62 s | 0.20 s | 0.36 s | 7.91× | 4.56× |
