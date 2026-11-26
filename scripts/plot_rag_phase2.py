"""Standard Matplotlib plots from actual measurements, placeholders for null metrics."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from python_controller.knowledge.experiments import read_jsonl

OUT=Path('docs/verification/rag_v3_phase2')
def main():
    metrics=json.loads((OUT/'metrics.json').read_text()); vm=json.loads((OUT/'verifier_metrics.json').read_text())
    arms=list(metrics); colors=['#64748b','#60a5fa','#22c55e'];target=OUT/'figures';target.mkdir(exist_ok=True)
    names={'faithfulness':'Faithfulness','coverage':'Required fact coverage','naturalness':'Korean naturalness',
           'raw_copy_rate':'Long prose copy rate','latency':'Actual end-to-end latency','cost':'Attributed calls and tokens',
           'confusion':'Semantic verifier (provisional labels, n=30)','false_rates':'Verifier false acceptance / rejection',
           'faith_coverage':'Faithfulness vs coverage','faith_latency':'Faithfulness vs latency',
           'natural_copy':'Naturalness vs copy','errors':'Pipeline response modes','risk_coverage':'Risk-coverage curve','pareto':'Pareto frontier'}
    for key,title in names.items():
        fig,ax=plt.subplots(figsize=(8,4.6));ax.set_title(title)
        if key=='latency':
            x=list(range(3)); ax.bar([i-.18 for i in x],[metrics[a]['latency_all_ms']['p50']/1000 for a in arms],.36,label='P50')
            ax.bar([i+.18 for i in x],[metrics[a]['latency_all_ms']['p95']/1000 for a in arms],.36,label='P95')
            ax.set_xticks(x,arms);ax.set_ylabel('seconds');ax.legend();ax.text(.02,.97,'3 questions per arm; P95/P99 are observed maxima\nB = shared-generation counterfactual',transform=ax.transAxes,va='top',fontsize=8)
        elif key=='cost':
            ax.bar(arms,[metrics[a]['api_call_count_attributed'] for a in arms],color=colors);ax.set_ylabel('Attributed inference calls')
            ax.text(.02,.95,'B reuses C generation; do not add B to physical calls',transform=ax.transAxes,va='top',fontsize=8)
        elif key=='raw_copy_rate':
            ax.bar(arms,[metrics[a]['raw_copy_rate'] for a in arms],color=colors);ax.set_ylabel('ratio vs shared actual retrieved context')
        elif key=='confusion':
            labels=['ENTAILMENT','CONTRADICTION','NEUTRAL'];pred=labels+['UNAVAILABLE']
            grid=[[vm['confusion_matrix'][g][p] for p in pred] for g in labels]
            ax.imshow(grid,cmap='Blues');ax.set_xticks(range(4),['E','C','N','U']);ax.set_yticks(range(3),['E','C','N'])
            for i in range(3):
                for j in range(4):ax.text(j,i,str(grid[i][j]),ha='center',va='center')
            ax.set_xlabel('Prediction');ax.set_ylabel('Provisional label')
        elif key=='false_rates':
            ax.bar(['False acceptance','False rejection'],[vm['false_acceptance_rate'],vm['false_rejection_rate']],color=colors[:2]);ax.set_ylim(0,1)
            ax.text(.02,.95,'n=30 templated provisional pairs; 0 observed is not 0 risk',transform=ax.transAxes,va='top',fontsize=8)
        elif key=='errors':
            counts={a:sum(r.get('answer_mode') in ('abstained','partial','error','unavailable') for r in read_jsonl(OUT/({'A':'baseline_v2','B':'generative_rag','C':'grounded_rag_v3'}[a]+'.jsonl'))) for a in arms}
            ax.bar(arms,list(counts.values()),color=colors);ax.set_ylabel('Partial/abstained/error outputs (not all errors)')
        else:
            ax.set_axis_off();ax.text(.5,.5,'Not measured / no valid confirmatory estimate\nIndependent final-output annotation pending\nNo calibrated threshold or defensible Pareto frontier',ha='center',va='center',transform=ax.transAxes)
        fig.tight_layout();fig.savefig(target/(key+'.png'),dpi=150);fig.savefig(target/(key+'.svg'));plt.close(fig)
if __name__=='__main__':main()
