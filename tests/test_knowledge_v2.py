import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from python_controller.knowledge.library import KnowledgeLibrary
from python_controller.knowledge.answer import answer_documents


class KnowledgeV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library=KnowledgeLibrary(Path(self.temp.name)/'library.db',Path(self.temp.name))
        self.library.vector=None

    def test_failed_replacement_preserves_active_revision_and_old_citation(self):
        first=self.library.register('manual.md',b'# Guide\nRobotSession has a verified STOP barrier.')
        old=self.library.search_robot_documents('RobotSession')[0]
        failed=self.library.register('manual.md',b'WIFI_PASSWORD=excludedpassword')
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(self.library.search_robot_documents('RobotSession')[0]['document_id'],first['id'])
        changed=self.library.register('manual.md',b'# Guide\nRobotSession uses a fresh STOP barrier.')
        self.assertNotEqual(changed['id'],first['id'])
        self.assertEqual(self.library.get_document_chunk(old['id'])['body'],old['body'])

    def test_reranker_missing_is_explicit_and_preserves_order(self):
        from python_controller.knowledge.reranker import rerank
        rows=[dict(id=1,body='hello'),dict(id=2,body='world')]
        result=rerank('hello',rows,Path(self.temp.name))
        self.assertEqual([r['id'] for r in result],[1,2])
        self.assertEqual(result[0]['rerank_status'],'unavailable')

    def test_reranker_deadline_terminates_its_process(self):
        import multiprocessing
        from python_controller.knowledge.reranker import rerank
        folder=Path(self.temp.name)
        (folder/'bge-reranker-v2-m3').mkdir()
        before={child.pid for child in multiprocessing.active_children()}
        result=rerank('query',[{'id':1,'body':'An evidence passage'}],folder,timeout=.01)
        self.assertEqual(result[0]['rerank_status'],'timeout')
        self.assertEqual({child.pid for child in multiprocessing.active_children()},before)

    def test_query_never_builds_passage_embeddings(self):
        from python_controller.knowledge.vector import VectorRanker
        self.library.register('manual.md',b'# Guide\nRobotSession controls the motor session.')
        ranker=VectorRanker(Path(self.temp.name))
        with self.library._db() as db,patch.object(ranker,'encode',return_value=[__import__('numpy').ones(384,dtype='<f4').tobytes()]) as encode:
            self.assertEqual(ranker.rank(db,'RobotSession'),[])
            encode.assert_not_called()

    def test_hardware_scope_cannot_leak_other_variant(self):
        for variant,code in [('PCB_V18_DESIGN','driver18'),('PCB_V19_DESIGN','driver19')]:
            self.library.documents()
            self.library._ingest(variant+'.md',f'# Driver\n{code} motor driver detail.'.encode(),'1','hardware_reference',metadata=(variant,'REFERENCE','UNVERIFIED',variant))
        rows=self.library.search_robot_documents('PCB V18 driver',mode='hybrid')
        self.assertTrue(rows)
        self.assertEqual({row['variant_id'] for row in rows},{'PCB_V18_DESIGN'})

    def test_synthesis_rejects_unknown_ids_and_nonexistent_support(self):
        self.library.documents()
        self.library._ingest('docs/manual.md',b'# RobotSession\nRobotSession controls the robot through verified STOP feedback.','rev','project_doc')
        with patch('python_controller.knowledge.answer._nemotron_evidence',return_value={'answers':[{'text':'Invented answer','sources':[{'id':999,'quote':'Invented quote'}]}]}):
            answer=answer_documents(self.library,'RobotSession')
        self.assertNotIn('Invented answer',answer['reply'])
        self.assertTrue(answer['citations'])

    def test_real_tokenizer_preserves_heading_table_and_limits(self):
        from python_controller.knowledge.chunking import chunks
        cache=Path(__file__).resolve().parents[1]/'logs/knowledge-models'
        if not any(cache.glob('models--intfloat--multilingual-e5-small/snapshots/*/tokenizer.json')):
            self.skipTest('Explicitly install the E5 tokenizer for token boundary checks')
        body='| Name | Value |\n| --- | --- |\n'+'\n'.join(f'| motor{i} | measured feedback |' for i in range(100))
        rows=list(chunks([('Robot / Motors',3,12,body)],cache))
        self.assertGreater(len(rows),1)
        self.assertTrue(all(row['token_count'] <= 400 for row in rows))
        self.assertTrue(all(row['page']==3 and row['heading_path']=='Robot / Motors' for row in rows))
        self.assertTrue(all('| Name | Value |' in row['body'] for row in rows))
        self.assertTrue(all(row['split_warning']=='split_structure' for row in rows))

    def test_background_embeddings_reuse_identical_input_across_revisions(self):
        from python_controller.knowledge.indexing import build
        import numpy as np
        from unittest.mock import Mock
        self.library.vector=Mock()
        self.library.vector.encode.return_value=[np.ones(384,dtype='<f4').tobytes()]
        with patch('python_controller.knowledge.indexing.schedule'):
            first=self.library.register('manual.md',b'# One\nRobotSession verified output feedback.\n# Two\nSecond section unchanged input.')
            self.assertTrue(build(self.library,first['id']))
            calls=self.library.vector.encode.call_count
            second=self.library.register('manual.md',b'# One\nRobotSession updated output feedback.\n# Two\nSecond section unchanged input.')
            self.assertTrue(build(self.library,second['id']))
        self.assertEqual(self.library.vector.encode.call_count,calls+1)
        doc=next(row for row in self.library.documents() if row['id']==second['id'])
        self.assertEqual(doc['embedding_status'],'ready')

    def test_rag_timeout_is_ninety_seconds_without_changing_control_client(self):
        import json
        from python_controller.knowledge.answer import _nemotron_evidence
        from python_controller.llm.nvidia_client import NvidiaPlanner
        environment={'DIG_KNOWLEDGE_CLOUD':'1','NVIDIA_API_KEY':'test-value','NVIDIA_MODEL':'test-model'}
        with patch('python_controller.llm_cli.load_environment',return_value=environment), patch('openai.OpenAI') as client:
            result=client.return_value.__enter__.return_value
            from types import SimpleNamespace
            result.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps({'answers':[]}),tool_calls=None))])
            _nemotron_evidence('question',[{'id':1,'body':'Approved project document evidence.','path':'docs/a.md','heading':'A','version':'v1','trust':'project_doc'}])
            self.assertEqual(client.call_args.kwargs['timeout'],54.)  # Shared 90s budget reserves time for the judge.
            with patch('python_controller.llm.nvidia_client.OpenAI') as control_client:
                NvidiaPlanner(api_key='test-value',model='test-model')._client()
                self.assertEqual(control_client.call_args.kwargs['timeout'],20)

    def test_unchanged_section_reuses_chunk_cache(self):
        from python_controller.knowledge.chunking import chunks
        cache=Path(__file__).resolve().parents[1]/'logs/knowledge-models'
        if not any(cache.glob('models--intfloat--multilingual-e5-small/snapshots/*/tokenizer.json')):
            self.skipTest('Cached tokenizer required')
        self.library.cache=cache
        with patch('python_controller.knowledge.chunking.chunks',wraps=chunks) as chunker:
            self.library.register('manual.md',b'# One\nFirst section text.\n# Two\nUnchanged second section text.')
            first=chunker.call_count
            self.library.register('manual.md',b'# One\nUpdated first section text.\n# Two\nUnchanged second section text.')
        self.assertEqual(chunker.call_count,first+1)

    def test_cloud_quote_numbers_resolve_to_exact_server_evidence(self):
        import json
        from python_controller.knowledge.answer import _nemotron_evidence
        row={'id':7,'body':'Qt uses PySide6 for the desktop interface.','heading':'Qt','path':'docs/qt.md','version':'v1','trust':'project_doc'}
        response={'claims':[{'claim_id':'c1','text':'Qt is a desktop interface.','supports':[{'evidence_id':7,'quote_index':0}]}],'unanswered_aspects':[]}
        env={'DIG_KNOWLEDGE_CLOUD':'1','NVIDIA_API_KEY':'test-value','NVIDIA_MODEL':'test-model'}
        with patch('python_controller.llm_cli.load_environment',return_value=env), patch('python_controller.llm.nvidia_client.NvidiaPlanner.complete',return_value=json.dumps(response)):
            selected=_nemotron_evidence('Qt?', [row])
        from python_controller.knowledge.evidence import select_evidence
        index=selected['claims'][0]['supports'][0]['quote_index']
        self.assertEqual(select_evidence([row],'Qt')[0]['quote_candidates'][index],row['body'])

    def test_new_revision_stays_hidden_until_embeddings_are_complete(self):
        from python_controller.knowledge.indexing import build
        import numpy as np
        from unittest.mock import Mock
        self.library.vector=Mock()
        self.library.vector.encode.return_value=[np.ones(384,dtype='<f4').tobytes()]
        with patch('python_controller.knowledge.indexing.schedule'):
            first=self.library.register('manual.md',b'# Guide\nOLDWORD is the previous manual revision.')
            self.assertEqual(first['status'],'preparing')
            self.assertEqual(self.library.search_robot_documents('OLDWORD',mode='fts'),[])
            self.assertTrue(build(self.library,first['id']))
            old=self.library.search_robot_documents('OLDWORD',mode='fts')[0]
            second=self.library.register('manual.md',b'# Guide\nNEWWORD is the updated manual revision.')
            self.assertEqual(self.library.search_robot_documents('OLDWORD',mode='fts')[0]['id'],old['id'])
            self.assertEqual(self.library.search_robot_documents('NEWWORD',mode='fts'),[])
            self.assertTrue(build(self.library,second['id']))
            self.assertEqual(self.library.search_robot_documents('NEWWORD',mode='fts')[0]['document_id'],second['id'])
            self.assertEqual(self.library.search_robot_documents('OLDWORD',mode='fts'),[])
            self.assertEqual(self.library.get_document_chunk(old['id'])['body'],old['body'])

    def test_slow_superseded_build_cannot_restore_an_old_revision(self):
        from python_controller.knowledge.indexing import build
        import numpy as np
        from unittest.mock import Mock
        self.library.vector=Mock()
        self.library.vector.encode.return_value=[np.ones(384,dtype='<f4').tobytes()]
        with patch('python_controller.knowledge.indexing.schedule'):
            old=self.library.register('manual.md',b'# Guide\nOLDWORD is outdated content.')
            new=self.library.register('manual.md',b'# Guide\nNEWWORD is the final content.')
            self.assertTrue(build(self.library,new['id']))
            self.assertTrue(build(self.library,old['id']))
        self.assertTrue(self.library.search_robot_documents('NEWWORD',mode='fts'))
        self.assertFalse(self.library.search_robot_documents('OLDWORD',mode='fts'))


if __name__=='__main__':
    unittest.main()
