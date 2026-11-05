# Related work

What prior benchmarks test, how TENOR differs, and what we take from them. For anyone placing TENOR in the literature.
Every source below was opened and checked at source on 4 November 2026 (title, authors, venue, ID). Sources we could not verify are not cited.

## How TENOR differs

TENOR's five differences, used in the last column:
**Products** (shipped products, through their agent interfaces) · **Many meetings** (memory across meetings, not one transcript) · **Retention** (what deleting transcripts costs) · **Same meetings** (one set of meetings across products) · **Integrity** (not revealing meetings the asker can't see).

| Work | What it tests | Data | How TENOR differs |
|---|---|---|---|
| **LongMemEval** [1] (ICLR 2025) | Chat-assistant memory: extraction, multi-session reasoning, time, updates, abstention | 500 questions in generated user–assistant chat histories | Meetings, not chat. Products, Retention |
| **LoCoMo** [2] (ACL 2024) | Very long-term conversational memory: QA, event summaries | 50 generated, human-edited dialogues, up to 35 sessions | Meetings, not chat. Products, Retention |
| **MemoryAgentBench** [3] (arXiv 2025) | Memory agents: retrieval, test-time learning, long-range understanding, selective forgetting | Long-context datasets recast as multi-turn input | Meetings. Retention tests forgetting forced by the product, not chosen by the agent |
| **BEAM** [4] (ICLR 2026) | Long-term memory in conversations up to 10M tokens | 100 generated conversations, 2,000 validated questions | Real meetings; Products |
| **RealMem** [5] (Findings of ACL 2026) | Memory across long project-oriented interactions | 2,000+ generated cross-session dialogues, 11 scenarios | Real meetings; Products, Retention |
| **QMSum** [6] (NAACL 2021) | Query-based summarisation of one meeting | 1,808 query–summary pairs over 232 meetings (AMI, ICSI, parliament) | Many meetings; Products; QA, not summaries |
| **MeetingBank** [7] (ACL 2023) | Meeting summarisation | City-council meetings; minutes aligned to meeting segments | Products, Many meetings, Retention |
| **MeetingQA** [8] (ACL 2023) | Extractive QA on one transcript | Questions asked by meeting participants, answers from the discussion | Many meetings; Products; free-form answers |
| **ELITR-Bench** [9] (COLING 2025) | Long-context LLMs as meeting assistants, under ASR noise | 271 hand-written questions on ELITR meetings | Products, Many meetings, Retention |
| **AutoMin 2025** [10] (shared task) | Minuting; new QA on transcripts, English and Czech | Project meetings, European Parliament | Products, Many meetings |
| **Meeting Delegate** [11] (arXiv 2025) | LLMs taking part in a meeting on a user's behalf | Real meeting transcripts | Answers after the meeting, from memory |
| **MeetBench-XL** [12] (AAAI 2026 workshop) | Enterprise meeting assistants: fact checks, cross-meeting analysis; trains a dual-policy agent | MeetAll: 231 enterprise meetings, 140 hours, bilingual | Products (black box, no training); Retention; Same meetings |
| **MeetingProbe / MARC** [13] (arXiv 2026) | Grounding failures of meeting assistants, found by adaptive question search | 3,000+ QA pairs on 20 QMSum transcripts, 3 LLMs | Products, not models given a transcript; Many meetings; Retention |
| **FActScore** [14] (EMNLP 2023) | Factual precision of long text, claim by claim | Biographies against Wikipedia | Notes checked against their own meeting's transcript |
| **MiniCheck** [15] (EMNLP 2024) | Is a claim grounded in a given document? Small, cheap checkers | LLM-AggreFact grounding benchmark | We apply the same question to product notes |
| **AI note-taker test** [16] (repo, Oct 2026) | Notes quality of Otter, Fireflies, Granola, Scribbl | 5 AMI meetings, run by Scribbl (one of the tools tested) | Same meetings, but notes only: no agent interface, no QA, no Retention |
| **AMI** [17], **ICSI** [18] corpora | Meeting recordings with transcripts and annotations | AMI 100 h (two-thirds role-played design meetings); ICSI ~70 h research meetings | TENOR replays 7 AMI meetings into the reference products; ICSI's longer real series are a candidate for later |

**Honest scope.** TENOR's AMI set is 7 meetings, and the earlier private study used 4 meetings that fit in context, so "Many meetings" is designed for, not yet measured. Integrity is designed, not run.

## The gap

**No benchmark we found tests shipped meeting products through the interface agents use.** Memory benchmarks [1–5] use chat or generated dialogue. Meeting benchmarks [6–13] give a model the transcript and grade the model.

**Retention is untested.** Products let users delete transcripts; prior work assumes the transcript is there. TENOR asks what an agent loses when only notes remain.

**Cross-product comparison exists only for notes.** The note-taker test [16] ran four products on the same 5 AMI meetings. TENOR adds capture, transcription, notes and answer measures on the same AMI meetings for two products (Granola and Wispr Flow; preliminary), and questions through each product's agent interface.

**Answer type matters as much as accuracy.** MeetingProbe [13] found failures were mostly discourse and pragmatics, not fact recall. TENOR also counts *wrong but confident* separately from *declined*.

## Lessons we take

- **Fact compression loses detail; we saw the same.** LongMemEval found that replacing sessions with extracted facts or summaries "negatively impacts QA performance due to information loss", except on multi-session reasoning [1, §5.2]. On real meetings our fact memory scored 59% vs 92% for Granola's agent, and 0 of 8 on "who said X".
- **Questions that match the extractor flatter it.** This lesson is ours, not LongMemEval's: on synthetic Northwind, questions targeting planted facts let our fact memory tie the transcript readers; real meetings reversed it ([`SYNTHETIC_DIAGNOSTICS`](archive/northwind/SYNTHETIC_DIAGNOSTICS.md)).
- **Write held-out questions to a published schema, with training and review.** QMSum's annotators first marked topics and text spans, then wrote queries from a fixed list of general and specific query types; they passed a pre-annotation stage and experts reviewed every annotation [6, §3.2]. Our fresh test (X1) should do the same, with the question schema fixed before, and independent of, any memory's extraction schema.
- **Random questions undercount failures.** Adaptive search found 2.5× more failures than random probing (7.1% vs 2.9%) [13]. Our question sets are fixed, so failure rates are lower bounds.
- **Split notes into claims, then check each against the source.** That is the shape of FActScore [14] and MiniCheck [15], and of our notes-faithfulness track. MiniCheck shows a small, trained checker can reach GPT-4 accuracy [15]; our cheap, untrained checker (Haiku) did not, and barely told good notes from bad ([`NOTES_FAITHFULNESS`](tracks/NOTES.md) §4).
- **Publish inputs when you have a stake.** The note-taker test was run by a vendor and published every input and grader answer [16]. TENOR publishes its code, AMI-derived questions and scores; the earlier private meetings are not published, only content-free aggregates.

## References

All accessed 2026-11-04.

1. D. Wu, H. Wang, W. Yu, Y. Zhang, K.-W. Chang, D. Yu. "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory." ICLR 2025. arXiv:2410.10813. https://arxiv.org/abs/2410.10813
2. A. Maharana, D.-H. Lee, S. Tulyakov, M. Bansal, F. Barbieri, Y. Fang. "Evaluating Very Long-Term Conversational Memory of LLM Agents." ACL 2024. arXiv:2402.17753. https://aclanthology.org/2024.acl-long.747/
3. Y. Hu, Y. Wang, J. McAuley. "Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions" (MemoryAgentBench). arXiv:2507.05257, 2025. https://arxiv.org/abs/2507.05257
4. M. Tavakoli, A. Salemi, C. Ye, M. Abdalla, H. Zamani, J. R. Mitchell. "Beyond a Million Tokens: Benchmarking and Enhancing Long-Term Memory in LLMs" (BEAM). ICLR 2026. arXiv:2510.27246. https://arxiv.org/abs/2510.27246
5. H. Bian, Z. Yao, S. Hu, Z. Xu, S. Zhang, Y. Guo, Z. Yang, X. Han, H. Wang, R. Chen. "RealMem: Benchmarking LLMs in Real-World Memory-Driven Interaction." Findings of ACL 2026. arXiv:2601.06966. https://aclanthology.org/2026.findings-acl.703/
6. M. Zhong, D. Yin, T. Yu, A. Zaidi, M. Mutuma, R. Jha, A. H. Awadallah, A. Celikyilmaz, Y. Liu, X. Qiu, D. Radev. "QMSum: A New Benchmark for Query-based Multi-domain Meeting Summarization." NAACL 2021. arXiv:2104.05938. https://arxiv.org/abs/2104.05938
7. Y. Hu, T. Ganter, H. Deilamsalehy, F. Dernoncourt, H. Foroosh, F. Liu. "MeetingBank: A Benchmark Dataset for Meeting Summarization." ACL 2023. arXiv:2305.17529. https://arxiv.org/abs/2305.17529
8. A. Prasad, T. Bui, S. Yoon, H. Deilamsalehy, F. Dernoncourt, M. Bansal. "MeetingQA: Extractive Question-Answering on Meeting Transcripts." ACL 2023. https://aclanthology.org/2023.acl-long.837/
9. T. Thonet, J. Rozen, L. Besacier. "ELITR-Bench: A Meeting Assistant Benchmark for Long-Context Language Models." COLING 2025. arXiv:2403.20262. https://arxiv.org/abs/2403.20262
10. K. Shinde, L. Besacier, O. Bojar, T. Thonet, T. Ghosal. "Findings of the Third Automatic Minuting (AutoMin) Challenge." arXiv:2509.13814, 2025. https://arxiv.org/abs/2509.13814
11. L. Hu, S. Yuan, X. Qin, J. Zhang, Q. Lin, D. Zhang, S. Rajmohan, Q. Zhang. "MEETING DELEGATE: Benchmarking LLMs on Attending Meetings on Our Behalf." arXiv:2502.04376, 2025. https://arxiv.org/abs/2502.04376
12. Y. Hu, J. Xu, B. Lu, Z. Cheng, H. Hu, R. Wu, L. Song. "MeetBench-XL: Calibrated Multi-Dimensional Evaluation and Learned Dual-Policy Agents for Real-Time Meetings." AAAI 2026 workshop. arXiv:2602.03285. https://arxiv.org/abs/2602.03285
13. S. Khairy, Y. Hosseinkashi, V. Gopal, R. Cutler. "Evaluation-as-Search: Adaptive Discovery of Grounding Failures in Meeting Assistants" (introduces MeetingProbe and the MARC evaluator). arXiv:2608.20392, 2026. https://arxiv.org/abs/2608.20392
14. S. Min, K. Krishna, X. Lyu, M. Lewis, W. Yih, P. W. Koh, M. Iyyer, L. Zettlemoyer, H. Hajishirzi. "FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation." EMNLP 2023. arXiv:2305.14251. https://arxiv.org/abs/2305.14251
15. L. Tang, P. Laban, G. Durrett. "MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents." EMNLP 2024. arXiv:2404.10774. https://arxiv.org/abs/2404.10774
16. Scribbl (repository owner msallese31). "AI note takers tested on five public meetings (October 2026)." GitHub. https://github.com/msallese31/ai-note-taker-test-2026
17. AMI Meeting Corpus, University of Edinburgh. https://groups.inf.ed.ac.uk/ami/corpus/
18. ICSI Meeting Corpus. https://groups.inf.ed.ac.uk/ami/icsi/
