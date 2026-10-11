# After the set-based exact stage (DD-21)

Same dataset, seed and commands as `../baseline/`, plus `--set-based-exact --profile` (the profiler adds a small overhead). Single runs on one laptop. At 20,000 entities three unprofiled repeats gave an exact stage of 2.94, 2.31 and 2.37 s and a whole run of 33.9, 33.4 and 33.4 s; the baseline was 140.74 s and 199.93 s. Results (auto, review, exact-correct counts) match the baseline at every size.
