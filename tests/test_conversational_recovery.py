"""Local knowledge integration, no model results used as quality evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from python_controller.knowledge.library import KnowledgeLibrary
from python_controller.knowledge.answer import answer_documents
from python_controller.knowledge.rendering import validate_final
from python_controller.llm.agent_graph import ConversationGraph

ROOT=Path(__file__).resolve().parents[1]


class ForbiddenModel:
    def interpret(self,*args,**kwargs):raise AssertionError('Local knowledge must not invoke intent API')
    def respond(self,*args,**kwargs):raise AssertionError('Local knowledge must not invoke response API')


class ConversationalRecoveryTests(unittest.TestCase):
    def test_source_binding_tolerates_only_line_endings_not_content_changes(self):
        from hashlib import sha256
        from python_controller.knowledge.program import source_matches
        original=b'first\r\nsecond\r\n'
        proof={'sha256':sha256(original).hexdigest(),'lf_sha256':sha256(original.replace(b'\r\n',b'\n')).hexdigest(),'excerpt':'second'}
        self.assertTrue(source_matches(original,proof))
        self.assertTrue(source_matches(b'first\nsecond\n',proof))
        self.assertFalse(source_matches(b'changed\nsecond\n',proof))

    @classmethod
    def setUpClass(cls):
        cls.folder=TemporaryDirectory();cls.addClassCleanup(cls.folder.cleanup)
        cls.library=KnowledgeLibrary(Path(cls.folder.name)/'knowledge.db',ROOT);cls.library.vector=None
        with patch.object(cls.library,'_seed',return_value=None):cls.library.documents()

    def answer(self,question):
        with patch('python_controller.llm_cli.load_environment',return_value={'DIG_KNOWLEDGE_CLOUD':'0'}),patch('python_controller.knowledge.generation.RAGCalls.generate',side_effect=AssertionError('No API')):
            return answer_documents(self.library,question)

    def test_arbitrary_profile_values_and_negative_preserved(self):
        self.library.register('another-profile.md','# 테스트인물\n직업: 탐험가\n소속: 작은 연구실\n좋아하는것: 연두색, 원피스(옷, 애니 아님)'.encode())
        result=self.answer('테스트인물이 누구야?')
        self.assertEqual(result['reply'].count('등록된 소개에 따르면'),1)
        for value in ('탐험가','작은 연구실','연두색','옷, 애니 아님'):self.assertIn(value,result['reply'])
        self.assertEqual(result['speech_text'],result['reply']);self.assertTrue(result['citations'])
        self.assertTrue(validate_final(result['reply'],result['claims']))
        self.assertFalse(validate_final(result['reply']+' 애니를 좋아해요.',result['claims']))

    def test_all_overview_scopes_work_in_actual_graph_without_model(self):
        model=ForbiddenModel();graph=ConversationGraph(model,self.library,model)
        for q in ('우리 프로그램을 좀 설명해 줄래?','우리 시스템은 뭘 하는 거야?','어떤 기능이 있어?','D.I.G.는 어떻게 작동해?','AI랑 ESP32는 어떻게 연결돼?','프로그램 구조를 쉽게 설명해 줘.'):
            with self.subTest(question=q),patch('python_controller.llm_cli.load_environment',return_value={'DIG_KNOWLEDGE_CLOUD':'0'}):
                result=graph.turn(q,[],None)
                self.assertEqual(result['rag']['verification_status'],'structured')
                self.assertTrue(result['citations']);self.assertEqual(result['rag']['speech_text'],result['reply'])
                self.assertIsNone(result['intent']);self.assertIsNone(result['card'])

    def test_followup_simplification_and_negative_profile_context(self):
        model=ForbiddenModel();graph=ConversationGraph(model,self.library,model)
        first=graph.turn('우리 프로그램 설명해 줘',[],None)
        second=graph.turn('좀 더 쉽게 설명해 줘',[],None)
        self.assertIn('로봇의 상태를 살펴보고',second['reply']);self.assertNotEqual(first['reply'],second['reply'])
        self.library.register('followup.md','# 연습인물\n전공: 로봇\n좋아하는 것: 원피스(옷, 애니 아님), 포켓몬'.encode())
        graph.turn('연습인물이 누구야?',[],None)
        result=graph.turn('원피스 애니를 좋아한다는 뜻이야?',[],None)
        self.assertIn('애니 아님',result['reply'])

    def test_policy_is_not_semantic_failure(self):
        self.library.register('special.md','# 특수 통신\n특수 프로토콜은 장치에서 메시지를 전달합니다.'.encode())
        result=self.answer('문서: 특수 통신 프로토콜 설명')
        self.assertEqual(result['reason_code'],'POLICY_DISABLED')
        self.assertEqual(result['verification_status'],'policy_disabled')

    def test_private_source_and_verifier_unavailable_are_distinct(self):
        self.library.register('private-local.md','# 사내 암호 체계\n사내 암호 체계는 외부 반출 제한 자료입니다.'.encode())
        with patch('python_controller.llm_cli.load_environment',return_value={'DIG_KNOWLEDGE_CLOUD':'1'}),patch('python_controller.knowledge.generation.RAGCalls.generate',side_effect=AssertionError('Private data')):
            result=answer_documents(self.library,'문서: 사내 암호 체계')
        self.assertEqual(result['reason_code'],'PRIVATE_SOURCE_RESTRICTED')

    def test_no_source_and_no_unconditional_motion_safety(self):
        result=self.answer('문서: zzznonexistent987654')
        self.assertEqual(result['reason_code'],'RETRIEVAL_MISS')
        result=self.answer('100cm 이동해도 무조건 안전해?')
        self.assertIn('보장할 수는 없',result['reply'])
        self.assertNotIn('무조건 안전해요',result['reply'])

    def test_complex_candidate_not_automatically_registered(self):
        with patch('python_controller.knowledge.recovery.pipeline.answer_candidate',side_effect=AssertionError('Experimental only')):
            result=self.answer('우리 프로그램 설명해 줘')
        self.assertEqual(result['verification_status'],'structured')


if __name__=='__main__':unittest.main()
