"""Isolated real Web/Agent path, outbound AI disabled, no robot controls.

No live flag: this entry point cannot be used for paid API evaluation.
"""
import argparse
from pathlib import Path
import sys
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8770);args=parser.parse_args()
    from python_controller.config import load_config
    from python_controller.dashboard_runtime import DashboardEngine
    from python_controller.llm.unified_agent import UnifiedAgent
    from python_controller.web_dashboard import WebDashboard,PreviewStream,handler
    from python_controller.llm.nvidia_client import NvidiaPlanner,NvidiaError
    import os
    # Process-local only. Existing server environment/defaults remain untouched.
    os.environ['DIG_KNOWLEDGE_CLOUD']='0';os.environ['DIG_KNOWLEDGE_VECTOR']='0';os.environ['DIG_KNOWLEDGE_RERANK']='0'
    def forbidden(*args,**kwargs):raise NvidiaError('NVIDIA_OFFLINE_PREVIEW_NO_REQUEST')
    NvidiaPlanner._client=forbidden
    root=Path(__file__).resolve().parents[1]
    folder=root/'.tmp/conversational-rag-preview'
    engine=DashboardEngine(load_config(),folder/'logs','SYNTHETIC_PREVIEW',logger=False)
    engine.central_ui=True
    agent=UnifiedAgent(engine)
    # Synthetic second person demonstrates data-driven fields, not a special response.
    agent.knowledge.register('preview-person.md','# 김연구\n직업: 연구원\n소속: 로봇 연구실\n좋아하는 것: 커피, 산책(등산 아님)'.encode())
    agent._append('assistant','격리 실험 서버예요. 로컬 지식 경로를 확인하며 외부 AI 호출과 실물 연결은 사용하지 않아요.')
    web=WebDashboard(engine,PreviewStream,agent,control_enabled=False)
    web.settings_dir=folder/'settings'
    try:
        with ThreadingHTTPServer(('127.0.0.1',args.port),None) as server:
            server.RequestHandlerClass=handler(web,server.server_port)
            print(f'OFFLINE ISOLATED SIMULATION http://127.0.0.1:{server.server_port}/',flush=True)
            server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        agent.close();engine.close();web.close_camera()


if __name__=='__main__':main()
