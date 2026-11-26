"""Actual Edge/Qt input-to-render checks; no NVIDIA completions or robot actions."""
import argparse
import json
import os
from pathlib import Path
import sys
from time import perf_counter,monotonic,sleep
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/verification/conversational_rag_recovery'
QUESTIONS=['홍승조가 누구야?','원피스 애니를 좋아한다는 뜻이야?','김연구가 누구야?',
 '우리 프로그램 설명해 줘','어떤 기능이 있어?','AI랑 ESP32는 어떻게 연결돼?',
 '좀 더 쉽게 설명해 줘','PCB V18과 V19 차이가 뭐야?','지금 로봇은 움직이고 있어?','100cm 이동해도 무조건 안전해?']


def record(q,message,elapsed):
    return {'question':q,'reply':message['content'],'speech_text':message.get('speech_text'),
            'answer_mode':message.get('answer_mode'),'status':message.get('verification_status'),
            'reason_code':message.get('reason_code'),'citations':message.get('citations',[]),
            'latency_ms':elapsed,'abstained':message.get('answer_mode')=='abstained',
            'evidence_type':'ACTUAL_LOCAL_AGENT_UI_NO_NVIDIA_CALLS'}


def web(port):
    from playwright.sync_api import sync_playwright
    origin=f'http://127.0.0.1:{port}'
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1100},service_workers='block')
        blocked=[]
        def gate(route):
            if route.request.url.startswith(origin+'/'):route.continue_()
            else:blocked.append(route.request.url);route.abort()
        context.route('**/*',gate)
        page=context.new_page();page.goto(origin+'/',wait_until='domcontentloaded',timeout=60000)
        page.wait_for_function("!document.getElementById('agent-history').hidden",timeout=60000)
        results=[]
        for index,q in enumerate(QUESTIONS):
            before=page.locator('#agent-history .dm-message').count();start=perf_counter()
            page.locator('#prompt').fill(q);page.locator('#plan-form').evaluate('(f)=>f.requestSubmit()')
            deadline=monotonic()+60
            message=None
            while monotonic()<deadline:
                state=context.request.get(origin+'/api/state').json()
                agent=state['agent'];messages=agent['messages']
                if not agent['busy'] and messages and messages[-1]['role']=='assistant' and any(m['role']=='user' and m['content']==q for m in messages):
                    message=messages[-1];break
                page.wait_for_timeout(150)
            if message is None:raise AssertionError('No response: '+q)
            page.wait_for_function('(text)=>Array.from(document.querySelectorAll("#agent-history .bubble")).some(e=>e.textContent===text)',arg=message['content'],timeout=15000)
            item=record(q,message,(perf_counter()-start)*1000);item['rendered_in_browser']=True;results.append(item)
            if index in (0,3,7):page.screenshot(path=str(OUT/f'web-answer-{index}.png'),full_page=True)
        sources=page.locator('#agent-history details.chat-citations')
        assert sources.count()>0
        sources.last.locator('summary').click()
        page.screenshot(path=str(OUT/'web-sources.png'),full_page=True)
        (OUT/'web_results.json').write_text(json.dumps({'url':origin,'browser':'Installed Microsoft Edge, headless actual HTML/JS rendering','external_requests_blocked':blocked,'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()


def qt():
    os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['DIG_KNOWLEDGE_CLOUD']='0';os.environ['DIG_KNOWLEDGE_VECTOR']='0'
    from PySide6.QtWidgets import QApplication
    from python_controller.config import load_config
    from python_controller.dashboard_runtime import DashboardEngine
    from python_controller.llm.unified_agent import UnifiedAgent
    from python_controller.visualization.agent_panel import AgentPanel
    from python_controller.llm.nvidia_client import NvidiaPlanner,NvidiaError
    def forbidden(*a,**k):raise NvidiaError('NVIDIA_OFFLINE_UI_CHECK_NO_REQUEST')
    NvidiaPlanner._client=forbidden
    app=QApplication.instance() or QApplication([])
    engine=DashboardEngine(load_config(),ROOT/'.tmp/conversational-qt/logs','SYNTHETIC_PREVIEW',logger=False);engine.central_ui=True
    agent=UnifiedAgent(engine);agent.knowledge.vector=None
    agent.knowledge.register('preview-person.md','# 김연구\n직업: 연구원\n소속: 로봇 연구실\n좋아하는 것: 커피, 산책(등산 아님)'.encode())
    panel=AgentPanel(agent,lambda page:None);panel.resize(1440,1000);panel.show();results=[]
    try:
        for q in QUESTIONS:
            start=perf_counter();panel.command.setText(q);panel.send();deadline=monotonic()+60
            while agent.busy and monotonic()<deadline:agent.poll();app.processEvents();sleep(.01)
            assert not agent.busy
            panel.refresh();app.processEvents();message=agent.view()['messages'][-1]
            assert message['role']=='assistant' and message['content'] in panel.history.toPlainText()
            item=record(q,message,(perf_counter()-start)*1000);item['rendered_in_qt']=True;results.append(item)
        assert panel.source_choice.count()>0
        panel.sources_toggle.setChecked(True);panel.show_source();app.processEvents()
        assert panel.source_text.toPlainText()
        panel.grab().save(str(OUT/'qt-final.png'))
        (OUT/'qt_results.json').write_text(json.dumps({'ui':'Real PySide6 AgentPanel, offscreen','results':results},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:panel.close();agent.close();engine.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ui',choices=('web','qt'),required=True);parser.add_argument('--port',type=int,default=8771);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    web(args.port) if args.ui=='web' else qt()
