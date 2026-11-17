import argparse, hashlib, json, itertools
from pathlib import Path
import numpy as np
import pandas as pd

TARGET=226714.0

def clean_json(value):
    if isinstance(value,dict): return {k:clean_json(v) for k,v in value.items()}
    if isinstance(value,list): return [clean_json(v) for v in value]
    if isinstance(value,(float,np.floating)) and not np.isfinite(value): return None
    return value


def validate(d,e):
    if not {'date','revenue','users'} <= set(d): raise ValueError('历史数据缺少 date/revenue/users')
    if not {'date','event_id','scale','type','partner_type'} <= set(e): raise ValueError('活动数据缺少字段')
    d=d.copy(); e=e.copy()
    for c in ['revenue','users']: d[c]=pd.to_numeric(d[c],errors='raise')
    if len(d)<285 or not np.isfinite(d[['revenue','users']]).all().all() or (d[['revenue','users']]<0).any().any(): raise ValueError('历史至少285天，数值必须有限且非负')
    if d.date.tolist()!=[f'D{i:04d}' for i in range(1,len(d)+1)]: raise ValueError('历史日期必须连续且从 D0001 开始')
    if not e.date.astype(str).str.fullmatch(r'F\d{3}').all(): raise ValueError('未来日期格式应为 F001-F090')
    e['day']=e.date.str[1:].astype(int)
    if e.day.duplicated().any() or not e.day.between(1,90).all() or e.event_id.duplicated().any(): raise ValueError('未来日期或活动编号重复，或日期超出90天')
    e['scale']=pd.to_numeric(e.scale,errors='raise')
    if not e.scale.between(.5,1.5).all() or not e.type.isin(['A','B','C']).all() or not e.partner_type.isin(['exclusive','standard','none']).all(): raise ValueError('活动属性不符合数据说明')
    return d,e

def fit(y):
    # Every parameter uses only observations available at this origin.
    positive=np.flatnonzero(y>0)
    if len(positive)<8: raise ValueError('非零收入记录不足8条')
    gaps=np.diff(positive); period=int(pd.Series(gaps).mode().iloc[0])
    if period<2 or period>30: raise ValueError('无法识别2至30天的活动周期，请人工复核')
    phase=int(pd.Series(positive%period).mode().iloc[0])
    alignment=float(np.mean(positive%period==phase))
    if alignment<.9: raise ValueError('活动周期对齐率低于90%，当前模型不适用')
    slots=np.flatnonzero(np.arange(len(y))%period==phase)
    sample=y[slots]
    return period,phase,sample,alignment

def predict(y,h=90,known_days=None,seed=42):
    period,phase,sample,alignment=fit(y)
    mask=(np.arange(len(y),len(y)+h)%period==phase) if known_days is None else np.isin(np.arange(1,h+1),known_days)
    mean=float(sample.mean()); point=mask.astype(float)*mean
    rng=np.random.default_rng(seed); n=int(mask.sum())
    # Empirical predictive bootstrap, including zero-revenue slots and rare spikes.
    draws=rng.choice(sample,(20000,n),replace=True).sum(axis=1) if n else np.zeros(20000)
    lo,hi=np.quantile(draws,[.05,.95]); q=np.quantile(sample,[.05,.95])
    return point,float(lo),float(hi),dict(period=period,phase_zero_based=phase,alignment=alignment,slot_count=len(sample),slot_mean=mean,zero_slot_rate=float(np.mean(sample==0)),daily_low=float(q[0]),daily_high=float(q[1]))

def run(d,e,target=TARGET):
    if not np.isfinite(target) or target<=0: raise ValueError('目标必须为正数')
    d,e=validate(d,e); y=d.revenue.to_numpy(float)
    rows=[]
    for origin in range(195,len(y)-89,90):
        train=y[:origin]; actual=y[origin:origin+90]
        pred,lo,hi,meta=predict(train,seed=origin)
        base=np.resize(train[-7:],90) # recursive seasonal-naive; no future actuals
        total=float(actual.sum()); denom=total if total>0 else np.nan
        rows.append(dict(origin=origin,test_start=origin+1,test_end=origin+90,actual=total,model_total=float(pred.sum()),baseline_total=float(base.sum()),model_wape=float(abs(actual-pred).sum()/denom),baseline_wape=float(abs(actual-base).sum()/denom),model_total_ape=float(abs(total-pred.sum())/denom),baseline_total_ape=float(abs(total-base.sum())/denom),model_mae=float(abs(actual-pred).mean()),baseline_mae=float(abs(actual-base).mean()),lower90=lo,upper90=hi,covered90=bool(lo<=total<=hi),**meta))
    bt=pd.DataFrame(rows)
    scores=[]
    for i,row in bt.iterrows():
        radius=max(scores)*row.model_total if len(scores)>=4 else np.nan
        bt.loc[i,'calibrated_lower90']=max(0,row.model_total-radius) if np.isfinite(radius) else np.nan
        bt.loc[i,'calibrated_upper90']=row.model_total+radius
        bt.loc[i,'calibrated_covered90']=float(abs(row.actual-row.model_total)<=radius) if np.isfinite(radius) else np.nan
        scores.append(abs(row.actual-row.model_total)/max(row.model_total,1))
    tests={}
    for metric in ['wape','total_ape']:
        diff=(bt['baseline_'+metric]-bt['model_'+metric]).dropna().to_numpy()
        # Exact two-sided paired sign-flip test; disjoint test windows, shared training remains a caveat.
        values=np.array([np.mean(diff*np.array(s)) for s in itertools.product([-1,1],repeat=len(diff))]) if len(diff)<=16 else (np.random.default_rng(42).choice([-1,1],(20000,len(diff)))*diff).mean(axis=1)
        pv=float(np.mean(abs(values)>=abs(diff.mean())-1e-12)) if len(diff) else float('nan')
        tests[metric]={'model_mean':float(bt['model_'+metric].mean()),'baseline_mean':float(bt['baseline_'+metric].mean()),'mean_gain':float(diff.mean()),'p_two_sided':pv,'significant_005':bool(pv<.05 and diff.mean()>0)}
    pred,lo,hi,meta=predict(y,known_days=e.day.to_numpy())
    fc=pd.DataFrame({'date':[f'F{i:03d}' for i in range(1,91)],'is_scheduled':np.isin(np.arange(1,91),e.day),'point':pred})
    fc['daily_lower90']=np.where(fc.is_scheduled,meta['daily_low'],0);fc['daily_upper90']=np.where(fc.is_scheduled,meta['daily_high'],0)
    fc=fc.merge(e.drop(columns='day'),on='date',how='left')
    features=d.copy(); features['day']=np.arange(1,len(d)+1);features['lag7']=d.revenue.shift(7); features['past_90_mean']=d.revenue.shift(1).rolling(90,min_periods=1).mean()
    # Diagnostic features only; lag7/users are NOT fed into future predictions.
    history=float(y.sum()); total=float(pred.sum())
    raw_lo,raw_hi=lo,hi
    # n=9 yields ceil((n+1)*.9)=9: maximum past relative error.
    radius=max(scores)*total
    if len(scores)>=4: lo,hi=max(0,total-radius),total+radius
    # With too few calibration windows retain the raw predictive range.
    period,phase,sample,alignment=fit(y)
    features['final_fit_period']=period
    features['final_fit_candidate_slot']=np.arange(len(y))%period==phase
    features['final_fit_slot_mean']=float(sample.mean())
    features['feature_scope']='final_fit_diagnostic_not_backtest_input'
    fc['cumulative_point']=fc.point.cumsum()
    fc['total90_point']=total
    fc['total90_lower_reference']=lo
    fc['total90_upper_reference']=hi
    summary=dict(interval_method='past_error_expansion' if len(scores)>=4 else 'raw_bootstrap_insufficient_calibration',raw_lower90=raw_lo,raw_upper90=raw_hi,calibrated_coverage=float(bt.calibrated_covered90.mean()),calibrated_windows=int(bt.calibrated_covered90.count()),history=history,target=target,forecast90=total,lower90=lo,upper90=hi,completion_pct=(history+total)/target*100,completion_lower_pct=(history+lo)/target*100,completion_upper_pct=(history+hi)/target*100,zero_fraction=float(np.mean(y==0)),positive_days=int(np.sum(y>0)),events=len(e),backtest_windows=len(bt),coverage90=float(bt.covered90.mean()),tests=tests,model=meta)
    alerts={'review_frequency':'每次活动结束及每周','rules':[{'signal':'未来活动日期、数量或取消状态变化','action':'立即替换日历并重算；旧预测作废'},{'signal':'连续3个计划活动收入为0','action':'暂停使用原预测，核查取消、入账延迟和业务变化'},{'signal':'最近5个活动平均收入偏离训练活动槽均值超过30%','action':'人工复核并用新数据重估；不自动覆盖旧预测'},{'signal':'出现非计划日收入或周期对齐率低于90%','action':'检查遗漏日历；停止使用当前周期模型'},{'signal':'最近10个成熟90天窗口的90%区间覆盖率低于80%','action':'撤回区间可信度声明，重新校准'}],'threshold_status':'管理阈值，未在本数据上优化或验证误报率'}
    return features,bt,fc,clean_json(summary),alerts

def save(results,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    for name,df in zip(['features','backtest','forecast'],results[:3]): df.to_csv(out/(name+'.csv'),index=False,encoding='utf-8-sig')
    for name,obj in zip(['summary','alert_rule'],results[3:]): (out/(name+'.json')).write_text(json.dumps(clean_json(obj),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--daily',default='data/C5_data_daily_series.csv');parser.add_argument('--events',default='data/C5_data_future_events.csv');parser.add_argument('--out',default='results');parser.add_argument('--target',type=float,default=TARGET);a=parser.parse_args()
    r=run(pd.read_csv(a.daily),pd.read_csv(a.events),a.target);save(r,a.out);print(json.dumps(r[3],ensure_ascii=False,indent=2))
