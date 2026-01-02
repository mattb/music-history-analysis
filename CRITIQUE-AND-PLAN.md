What you’ve built is a nicely “orthogonal” stack: (1) personal behavior (scrobbles) → a user-conditioned similarity space, (2) expert behavior (year-end lists) → a critic-conditioned similarity space, (3) metadata enrichment (MusicBrainz dumps) → context and faceting, and (4) temporal analysis → narrative. That combination is relatively uncommon in academic recsys work, which often leans heavily on collaborative filtering and large population-scale interaction logs; your design is closer to “personal analytics + expert signals + transparent explanations” than to “optimize engagement.”

Comparable strategies, datasets, and model families (with papers)

For listening-history / implicit-feedback baselines, your “co-occurrence → normalization → SVD” approach is in the same lineage as classic implicit factorization and item–item similarity methods, but you’re applying it at artist level with a time-window co-occurrence definition. The closest “off the shelf” analogue is item-embedding from co-occurrence with SGNS/skip-gram (Item2Vec), which often beats or matches SVD on item similarity while being easier to update incrementally. ([arXiv][1])

For public datasets similar to your inputs:

* Large-scale listening logs: LFM-1b (1B+ Last.fm events) is the canonical research reference point for exactly the kind of “timestamped listens with artist/album/track” data you’re modeling. ([JKU Campus Portal][2])
* Smaller Last.fm user–artist playcount corpora exist too (e.g., 360K users) and are widely used for artist-level modeling. ([Universitat Pompeu Fabra][3])
* ListenBrainz provides public “listens” dumps (open alternative ecosystem) that align well with your “local-first, batch” posture. ([Read the Docs][4])
* Million Song Dataset (MSD) is still a standard reference for large-scale music research metadata/audio features. ([Columbia Engineering][5])
* MSD’s Taste Profile Subset is a classic implicit-feedback matrix (user–track play counts), useful as a benchmarking analogue to parts of your pipeline even if you don’t want CF in-product. ([Million Song Dataset][6])
* Playlist corpora: Spotify’s Million Playlist Dataset (RecSys Challenge 2018) is the big public benchmark for “sequence-ish co-occurrence” modeling, and the challenge writeup summarizes what worked. ([ACM Digital Library][7])
* Very large modern interaction datasets now exist openly too (e.g., Yambda-5B with billions of interactions and provided audio embeddings), which is relevant if you ever want optional pretrained “global priors” without using other users’ data at runtime. ([arXiv][8])

For “critics/reviews as signals,” there are a few adjacent research artifacts you can borrow ideas from:

* MARD (Multimodal Album Reviews Dataset): large-scale album review texts enriched with MusicBrainz metadata (and historically AcousticBrainz descriptors), useful as a research comparator for “textual critique + music entities.” ([Universitat Pompeu Fabra][9])
* P4KxSpotify: Pitchfork reviews mapped to Spotify audio features, a clean example of connecting critical writing to structured/audio descriptors. ([AAAI Conference Proceedings][10])

For model families you’re near today, and the next step up:

* Sequential/session recommendation: GRU4Rec and SASRec are foundational references if you decide to model “what comes next” or “what tends to follow” at track/artist/album level rather than only weekly co-occurrence. ([arXiv][11])
* Important caution: strong baselines like session-kNN can outperform neural methods in some session settings; worth keeping in mind if you add sequence models. ([ACM Digital Library][12])
* Knowledge-graph recommendation: there’s active work on using open music KGs and embeddings to improve diversity/explainability—philosophically aligned with your transparency goals. ([ScienceDirect][13])

For “audio embeddings / content representations” (even if optional):

* OpenL3 is a widely used open audio embedding baseline. ([Justin Salamon][14])
* CLMR is a strong self-supervised representation learning approach for music audio. ([arXiv][15])
* MERT is a large-scale self-supervised music understanding model and MARBLE is a benchmark suite around these representations. ([arXiv][16])
* MuLan and CLAP are two-tower audio–text embedding models; even if you never ship them, they’re useful conceptual templates for “bridge personal taste ↔ language ↔ music.” ([arXiv][17])

Critique (what’s strong, and where it will bite you)

Your biggest strength is conceptual cleanliness: two distinct similarity spaces with explicit provenance, then cross-space analysis to expose disagreements. That’s rare and compelling.

The biggest technical risk is entity resolution. Your normalization + fuzzy matching will work “most of the time,” but the long tail (deluxe editions, reissues, same-title albums, transliterations, multiple release groups, collaborations, “Various Artists,” split scrobbles across aliases) can skew overlap, embeddings, and trend signals in subtle ways. MusicBrainz dumps help, but the moment you key things by (artist, album) strings, you’re accepting hard-to-debug leakage between entities. MusicBrainz explicitly supports JSON dumps and richer identifiers; you’ll get outsized returns by pushing more of your pipeline onto stable IDs (artist MBID, release-group MBID) wherever possible. ([MusicBrainz][18])

Your “5x5 album heard” rule is a practical heuristic, but it’s discontinuous (4 plays vs 5 plays flips state) and biased against short records (EPs), long albums with a few favorites, and listening modes like “single-track obsession.” It will also vary by era (you listened differently in 2007 vs 2025) and by genre (ambient vs punk vs pop). Good heuristic, but you’ll want a soft scoring version to avoid brittle downstream decisions.

The co-occurrence definition (“weeks where both artists played”) is intuitive for taste-association, but it confounds at least four things: (1) seasonality / life periods, (2) release cycles and media exposure, (3) “background staples” you play constantly, and (4) binge phases. Your geometric-mean normalization helps popularity bias, but it doesn’t separate “I always listen to X” from “X is a bridge between two phases,” and it can over-credit artists that are simply ubiquitous in your history.

Critics-space embeddings are a great idea, but list co-occurrence is extremely noisy as a proxy for “similarity.” Publications have house styles; list lengths vary wildly; rank information is thrown away; and “same list” can mean “same year” rather than “same sound.” If you keep the matrix approach, you’ll probably want to incorporate rank/weighting and publication-level calibration, otherwise you risk encoding editorial structure as “musical proximity.”

Finally, evaluation is currently the thinnest part. You have “critic accuracy tracking,” but you don’t yet have a rigorous loop that tells you which modeling choices improve outcomes for the user (novelty, satisfaction, rediscovery, serendipity, reduced regret) versus just changing numbers.

Roadmap for improvement and expansion

Phase 1: Make the foundation harder to break (high ROI)

1. Upgrade entity resolution to “IDs first” wherever possible. Treat MBIDs as primary keys, and fall back to normalized strings only when IDs are missing. Store alias mappings (artist name variants, “feat.” patterns, split artists) and surface a “match confidence” on every join so users can audit bad matches.

2. Replace binary “heard” with a continuous “album familiarity score.” Example: a monotone function of (unique tracks, total plays, play dispersion, recency), then allow the CLI to apply thresholds per command (“for overlap, require familiarity ≥ 0.6”; for regret, maybe ≥ 0.2). Keep your 5x5 as a preset, but stop baking it into the data model.

3. Make time windows multi-resolution. Keep weeks, but also compute sessions (e.g., 30–60 minute gaps) and months/quarters. “Gateway artists” detected at session-level can look very different (and often more causal) than at week-level.

4. Add uncertainty and diagnostics to outputs. When showing critic overlap, show how many items matched at high confidence vs fuzzy; when showing “divergent neighborhoods,” show counts and stability (do the neighbors persist across slight parameter changes?).

Phase 2: Improve the two embedding spaces (without losing transparency)
5) Benchmark alternative embedding constructions against your current SVD:

* PMI/PPMI + SVD (often very strong for co-occurrence)
* Item2Vec/SGNS on session or windowed sequences (incremental-friendly) ([arXiv][1])
  Do this as an internal “model bake-off” with a fixed evaluation harness (next bullet).

6. Add a lightweight offline evaluation harness tied to your goals:

* “Future holdout”: train embeddings on history up to year Y, measure whether nearest-neighbor expansions recover artists you actually started listening to in Y+1…Y+2.
* “Rediscovery”: hide a known “rediscovered” artist’s comeback period; see if the system surfaces it earlier as a bridge/neighbor.
* “Critic follow-through”: from aligned critics’ past picks, predict which you later play ≥N times.
  This gives you a way to choose between SVD vs item2vec vs session-based variants without arguing from taste.

7. Upgrade critics modeling from “co-listed = similar” to “critic as a taste vector.”
   Instead of only embedding artists, embed each critic/publication as a vector in the same space (e.g., average/attention-weighted artists on their lists, with rank weights). Then your critic matching becomes a clean nearest-neighbor problem with regularization, and you can answer: “which critics drifted away from you over time?” This aligns with research showing expert recommendations can drive novelty/diversity, but impact depends on calibration. ([ScienceDirect][19])

8. Incorporate rank and list-length normalization. A #1 album should contribute more than #50; and a 10-item list shouldn’t be treated the same as a 100-item list. Even simple weighting (e.g., reciprocal rank) will likely reduce noise.

Phase 3: Add new information channels (optional, but powerful)
9) Optional audio/text embedding layer for “why does this match?” and cold-start within your own library:

* Local-first option: if the user has audio files, compute OpenL3 or MERT embeddings locally. ([GitHub][20])
* “Metadata-text” option: embed album/artist descriptions, tags, and review snippets in a text embedding space; align to critics and your history.
* Stretch: use a joint audio–text model conceptually like MuLan/CLAP to unify “sound” and “words,” but keep it optional and offline where possible. ([arXiv][17])

10. Expand metadata beyond MusicBrainz tags (which can be sparse/uneven):

* Add Wikidata/Discogs-style attributes if licensing allows (countries, scenes, membership graphs).
* Treat genres as distributions (not top-5 strings) and keep provenance (which source asserted what).

11. Knowledge-graph mode (still explainable, still local-first):
    Build a local KG of Artist–Album–Label–Country–Year–CriticList edges, then run graph algorithms for “bridge” and “influence” (personal) rather than only embedding-neighborhood heuristics. This also gives you richer explanations (“this recommendation is 2 hops from Warp Records + 3 aligned critics”). The KG literature in music emphasizes diversity/explainability as a win condition, which matches your philosophy. ([ScienceDirect][13])

Phase 4: Productize the insights (make it feel like a “music microscope”)
12) Add “story modes” that stitch your existing analyses into narratives:

* “Eras” + defining artists + what changed + what bridged the change
* “Parallel selves”: your-space vs critics-space disagreements as a guided tour
* “Regrets you’ll probably like”: backed by critic alignment + similarity + metadata facets

13. Make scraping/rights explicit and resilient.
    Your critics pipeline depends on a single aggregator site and ongoing HTML stability. Consider supporting import formats (CSV/JSON) so users can bring their own lists, and/or integrate with existing public datasets where available (e.g., Pitchfork-linked datasets) to reduce fragility. ([AAAI Conference Proceedings][10])

If you want a very concrete next step: I’d start by (a) adding confidence-scored entity resolution with MBID-first joins, and (b) building the evaluation harness for embedding bake-offs (SVD vs PPMI+SVD vs Item2Vec). That will immediately tell you where your current approach is “good enough” and where it’s leaving a lot on the table.

[1]: https://arxiv.org/pdf/1603.04259?utm_source=chatgpt.com "[PDF] ITEM2VEC: NEURAL ITEM EMBEDDING FOR COLLABORATIVE ..."
[2]: https://www.cp.jku.at/people/schedl/Research/Publications/pdf/schedl_icmr_2016.pdf?utm_source=chatgpt.com "The LFM-1b Dataset for Music Retrieval and Recommendation"
[3]: https://www.upf.edu/web/mtg/lastfm360k?utm_source=chatgpt.com "Last.fm dataset 360K - MTG - Music Technology Group"
[4]: https://media.readthedocs.org/pdf/listenbrainz-server/latest/listenbrainz-server.pdf?utm_source=chatgpt.com "ListenBrainz Documentation"
[5]: https://www.ee.columbia.edu/~dpwe/pubs/BertEWL11-msd.pdf?utm_source=chatgpt.com "THE MILLION SONG DATASET"
[6]: https://millionsongdataset.com/tasteprofile/?utm_source=chatgpt.com "The Echo Nest Taste Profile Subset"
[7]: https://dl.acm.org/doi/10.1145/3240323.3240342?utm_source=chatgpt.com "Recsys challenge 2018: automatic music playlist continuation"
[8]: https://arxiv.org/html/2505.22238v2?utm_source=chatgpt.com "Yambda-5B — A Large-Scale Multi-modal Dataset for ..."
[9]: https://www.upf.edu/web/mtg/mard?utm_source=chatgpt.com "MARD: Multimodal Album Reviews Dataset - MTG"
[10]: https://ojs.aaai.org/index.php/ICWSM/article/download/7355/7209/10585?utm_source=chatgpt.com "P4KxSpotify: A Dataset of Pitchfork Music Reviews and ..."
[11]: https://arxiv.org/abs/1511.06939?utm_source=chatgpt.com "Session-based Recommendations with Recurrent Neural ..."
[12]: https://dl.acm.org/doi/10.1145/3109859.3109872?utm_source=chatgpt.com "When Recurrent Neural Networks meet the Neighborhood ..."
[13]: https://www.sciencedirect.com/science/article/pii/S0957417423008497?utm_source=chatgpt.com "I am all EARS: Using open data and knowledge graph ..."
[14]: https://www.justinsalamon.com/uploads/4/3/9/4/4394963/cramer_looklistenlearnmore_icassp_2019.pdf?utm_source=chatgpt.com "Look, Listen and Learn More: Design Choices for Deep ..."
[15]: https://arxiv.org/abs/2103.09410?utm_source=chatgpt.com "Contrastive Learning of Musical Representations"
[16]: https://arxiv.org/abs/2306.00107?utm_source=chatgpt.com "MERT: Acoustic Music Understanding Model with Large-Scale Self-supervised Training"
[17]: https://arxiv.org/abs/2208.12415?utm_source=chatgpt.com "MuLan: A Joint Embedding of Music Audio and Natural ..."
[18]: https://musicbrainz.org/doc/Development/JSON_Data_Dumps?utm_source=chatgpt.com "Development / JSON Data Dumps"
[19]: https://www.sciencedirect.com/science/article/abs/pii/S0167923620301007?utm_source=chatgpt.com "Measuring the impact of expert recommendations"
[20]: https://github.com/marl/openl3?utm_source=chatgpt.com "OpenL3: Open-source deep audio and image embeddings"
