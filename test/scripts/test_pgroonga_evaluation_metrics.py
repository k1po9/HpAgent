"""Check benchmark math with independent hand-calculated examples."""
import importlib.util
import math
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    'pgroonga_evaluation', Path(__file__).resolve().parents[2] /
    'scripts/experiments/hindsight_pgroonga.py')
EVAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVAL)


class EvaluationMetricsTest(unittest.TestCase):
    def test_recall_mrr_and_graded_ndcg(self):
        result = EVAL.rank_metrics(['noise', 'b', 'a'], {'a': 2, 'b': 1}, 3)
        self.assertEqual(result['recall'], 1)
        self.assertEqual(result['mrr'], .5)
        self.assertAlmostEqual(result['ndcg'],
            (1 / math.log2(3) + 3 / math.log2(4)) / (3 + 1 / math.log2(3)))

    def test_duplicate_docs_do_not_inflate_recall(self):
        result = EVAL.rank_metrics(['a', 'a', 'noise', 'b'], {'a': 2, 'b': 1}, 2)
        self.assertEqual(result['recall'], .5)

    def test_failed_answerable_query_is_zero(self):
        result = EVAL.rank_metrics([], {'a': 2}, 5)
        self.assertEqual((result['recall'], result['mrr'], result['ndcg']), (0, 0, 0))

    def test_no_answer_uses_injection_denominator(self):
        self.assertEqual(EVAL.rank_metrics(['a'], {}, 5)['false_injection'], 1)
        self.assertEqual(EVAL.rank_metrics([], {}, 5)['false_injection'], 0)
        self.assertIsNone(EVAL.rank_metrics([], {}, 5)['recall'])

    def test_trace_whitelist(self):
        trace = {'query': {'text': 'do not save', 'vector': [1, 2]},
                 'retrieval_results': [{'method_name': 'bm25', 'fact_type': 'world',
                     'results': [{'node_id': 'f', 'rank': 1, 'score': 2,
                                  'text': 'do not save', 'embedding': [3, 4]}]}]}
        result = EVAL.safe_trace(trace, {'f': 'd'})
        self.assertEqual(result['keyword'][0], dict(fact_id='f', document_id='d',
            rank=1, score=2, arm='bm25', fact_type='world'))
        self.assertNotIn('do not save', str(result))
        with self.assertRaises(KeyError):
            EVAL.safe_trace(trace, {})

    def test_observation_expands_sources_after_result_cutoff(self):
        items = [{'document_id': 'obs', 'source_documents': ['other', 'gold']},
                 {'document_id': 'noise'}]
        result = EVAL.stage_metrics(items, {'gold': 2}, 1)
        self.assertEqual(result['recall'], 1)
        self.assertEqual(result['ndcg'], 1)
        self.assertEqual(result['mrr'], 1)

    def test_observation_duplicate_source_does_not_inflate_gain(self):
        items = [{'document_id': 'obs', 'source_documents': ['gold']},
                 {'document_id': 'gold'}]
        self.assertEqual(EVAL.stage_metrics(items, {'gold': 2}, 2)['ndcg'], 1)
