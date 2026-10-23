"""Frozen quantile physics and schedule-cost decisions; no training or truth lookup."""
from __future__ import annotations

import numpy as np


def weighted_quantile(values, weights, q):
    values,weights=np.asarray(values,dtype=float),np.asarray(weights,dtype=float)
    if values.shape!=weights.shape or not 0<=q<=1 or not np.isfinite(values).all() or not np.isfinite(weights).all() or (weights<0).any() or weights.sum()<=0:
        raise ValueError('invalid weighted distribution')
    mask=weights>0;order=np.argsort(values[mask],kind='stable');x=values[mask][order];w=weights[mask][order]
    return float(x[min(np.searchsorted(np.cumsum(w),q*w.sum(),side='left'),len(x)-1)])


def joint_projection(raw):
    values=np.asarray(raw,dtype=float)
    if values.ndim!=2 or values.shape[1]!=4:raise ValueError('expected rows×[T50,T90,A50,A90]')
    out=values.copy();finite=np.isfinite(values).all(axis=1);out[~finite]=np.nan
    x=out[finite].copy()
    counts={'invalid_rows':int((~finite).sum()),'rows':len(values),
        'raw_negative_heads':np.sum(values[finite]<0,axis=0).astype(int).tolist(),
        'raw_target_order_violations':int(((x[:,1]<x[:,0])|(x[:,3]<x[:,2])).sum()),
        'raw_physical_violations':int(((x[:,2]<x[:,0])|(x[:,2]>x[:,0]+15)|(x[:,3]<x[:,1])|(x[:,3]>x[:,1]+15)).sum())}
    original=x.copy();x=np.maximum(x,0);x[:,1]=np.maximum(x[:,1],x[:,0]);x[:,3]=np.maximum(x[:,3],x[:,2])
    x[:,2]=np.clip(x[:,2],x[:,0],x[:,0]+15)
    x[:,1]=np.maximum.reduce([x[:,1],x[:,0],x[:,3]-15])
    x[:,3]=np.maximum.reduce([x[:,3],x[:,2],x[:,1]])
    out[finite]=x;counts['projected_heads']=np.sum(x!=original,axis=0).astype(int).tolist()
    counts['projected_rows']=int(np.any(x!=original,axis=1).sum())
    return out,counts


def calibrated_projection(raw, corrections):
    corrections=np.asarray(corrections,dtype=float)
    if corrections.shape!=(2,) or not np.isfinite(corrections).all() or (corrections<0).any():raise ValueError('invalid residual corrections')
    precal,first_counts=joint_projection(raw);added=precal.copy();added[:,1]+=corrections[0];added[:,3]+=corrections[1]
    final,last_counts=joint_projection(added)
    return {'raw':np.asarray(raw,dtype=float).copy(),'precal':precal,'cal_added':added,'final':final,
            'precal_counts':first_counts,'final_counts':last_counts}


def residual_corrections(precal_predictions, target_bags, bag_weights):
    predictions=np.asarray(precal_predictions,dtype=float);residuals=[[],[]];weights=[]
    if len(predictions)!=len(target_bags) or len(target_bags)!=len(bag_weights):raise ValueError('calibration bag mismatch')
    for row,targets,weight in zip(predictions,target_bags,bag_weights):
        targets=np.asarray(targets,dtype=float)
        if targets.ndim!=2 or targets.shape[1]<2 or not len(targets):raise ValueError('calibration requires actual trip targets')
        if not np.isfinite(row).all() or not np.isfinite(targets[:,:2]).all():raise ValueError('invalid calibration values must not become zeros')
        residuals[0].extend(targets[:,0]-row[1]);residuals[1].extend(targets[:,1]-row[3])
        weights.extend([weight/len(targets)]*len(targets))
    return np.asarray([max(0,weighted_quantile(r,weights,.9))for r in residuals])


def choose(candidates, nominal, deadline, cost=.5, qualified=True):
    if cost not in (0,.25,.5,1):raise ValueError('cost outside predeclared profiles')
    if len(candidates)!=5 or {r['shift']for r in candidates}!={-30,-15,0,15,30}:return {'status':'data_insufficient','selected':None,'fallback':nominal}
    if not np.isfinite(deadline):return {'status':'data_insufficient','selected':None,'fallback':nominal,'reason':'invalid_deadline'}
    for row in candidates:
        p=np.asarray(row['prediction'],dtype=float)
        if p.shape!=(4,) or not np.isfinite(p).all():return {'status':'data_insufficient','selected':None,'fallback':nominal}
        T50,T90,A50,A90=p
        if min(p)<0 or T90<T50 or A90<A50 or not T50<=A50<=T50+15 or not T90<=A90<=T90+15:
            return {'status':'data_insufficient','selected':None,'fallback':nominal,'reason':'invalid_joint_quantiles'}
    if not qualified:return {'status':'strategy_unqualified','selected':None,'fallback':nominal}
    base=next(r for r in candidates if r['shift']==0);base_T=base['prediction'][0]
    feasible=[r for r in candidates if nominal+r['shift']+r['prediction'][3]<=deadline]
    if not feasible:return {'status':'no_feasible_option','selected':None,'fallback':nominal}
    accepted=[r for r in feasible if r['shift']==0 or (base_T-r['prediction'][0]>=3 and base_T-r['prediction'][0]-cost*abs(r['shift'])>0)]
    if not accepted:return {'status':'no_qualified_change','selected':None,'fallback':nominal}
    best=min(accepted,key=lambda r:(r['prediction'][0]+cost*abs(r['shift']),r['shift']!=0,abs(r['shift']),nominal+r['shift']))
    return {'status':'keep_nominal'if best['shift']==0 else 'change_historical_shadow_only',
            'selected':nominal+best['shift'],'shift':best['shift'],'fallback':nominal,'cost_profile':cost}


def validate_fit_plan(jobs, limit=12):
    if len({j['id']for j in jobs})!=len(jobs):raise ValueError('same fit ID cannot run again')
    count=sum(j['learned_heads']for j in jobs)
    if any(j['learned_heads']<=0 for j in jobs)or count>limit:raise ValueError('learned-head fit budget exceeded')
    return count


def common_calibration_mask(predictions, keys, bag_sizes):
    """One fixed case-key intersection for fair method ranking, not eligibility."""
    import hashlib,json
    methods=('HGB','TCN_ensemble','empirical','current_proxy')
    if set(predictions)!=set(methods)or len(keys)!=len(set(keys)):raise ValueError('common method/key contract mismatch')
    mask=np.asarray(bag_sizes)>=30
    if mask.shape!=(len(keys),):raise ValueError('bag size/key mismatch')
    for method in methods:
        values=np.asarray(predictions[method],dtype=float)
        if values.shape!=(len(keys),4):raise ValueError('prediction/key shape mismatch')
        mask&=np.isfinite(values).all(axis=1)
    selected=[list(key)for key,valid in zip(keys,mask)if valid]
    return mask,{'total_cases':len(keys),'common_cases':int(mask.sum()),
        'common_keys':selected,'key_sha256':hashlib.sha256(json.dumps(selected,separators=(',',':')).encode()).hexdigest(),
        'eligibility_issue_denominator_changed':False}


def score_quantiles(predictions,keys,bags,mask=None):
    """Actual-trip pinball/coverage with original equal case weights retained."""
    values=np.asarray(predictions,dtype=float)
    if values.shape!=(len(keys),4):raise ValueError('prediction/key shape mismatch')
    valid=np.isfinite(values).all(axis=1)
    if mask is not None:
        mask=np.asarray(mask,dtype=bool)
        if mask.shape!=valid.shape:raise ValueError('metric mask shape mismatch')
        valid&=mask
    numerator=np.zeros(4);coverage=np.zeros(4);mass=0.;trips=0;cases=0;q=np.asarray([.5,.9,.5,.9])
    for prediction,(day,nominal,shift),accepted in zip(values,keys,valid):
        bag=np.asarray(bags[(day,nominal+shift)])
        if not accepted or len(bag)<30:continue
        error=bag[:,[0,0,1,1]]-prediction
        numerator+=np.maximum(q*error,(q-1)*error).mean(axis=0)/60
        coverage+=(error<=0).mean(axis=0)/60;mass+=1/60;cases+=1;trips+=len(bag)
    return {'case_count':cases,'expanded_trip_case_rows':trips,'original_weight_mass':mass,
        'pinball_by_head':(numerator/mass).tolist()if mass else [None]*4,
        'mean_four_head_pinball':float(numerator.sum()/(4*mass))if mass else None,
        'coverage_by_head':(coverage/mass).tolist()if mass else [None]*4,
        'mask_applied_without_day_reweighting':True}


def evaluate_schedule(predictions,keys,bags,cost=.5,fixed_shift=None):
    """Exploratory candidate-policy evaluation; every issue retains a fallback."""
    grouped={}
    for key,prediction in zip(keys,np.asarray(predictions)):
        day,nominal,shift=key;grouped.setdefault((day,nominal),[]).append({'shift':shift,'prediction':prediction})
    rows=[]
    for (day,nominal),candidates in grouped.items():
        deadline=nominal+45
        if fixed_shift is None:decision=choose(candidates,nominal,deadline,cost,qualified=True)
        else:decision={'status':'fixed_action_baseline','selected':nominal+fixed_shift,'shift':fixed_shift,'fallback':nominal}
        selected=decision['selected']if decision['selected']is not None else decision['fallback']
        baseline=np.asarray(bags[(day,nominal)]);chosen=np.asarray(bags[(day,selected)])
        row={'day':day,'nominal':nominal,'deadline':deadline,'status':decision['status'],
            'selected':selected,'shift':selected-nominal,'fallback_applied':decision['selected']is None,
            'cost_profile':cost,'nominal_trips':len(baseline),'selected_trips':len(chosen)}
        if len(baseline)>=30 and len(chosen)>=30:
            median0=weighted_quantile(baseline[:,0],np.ones(len(baseline)),.5)
            median1=weighted_quantile(chosen[:,0],np.ones(len(chosen)),.5)
            roadgain=median0-median1;penalty=cost*abs(selected-nominal)
            row.update({'outcomes_available':True,'observed_nominal_Tq50':median0,'observed_selected_Tq50':median1,
                'observed_roadgain':roadgain,'shift_penalty':penalty,'observed_netgain':roadgain-penalty,
                'nominal_ontime_share':float(np.mean(baseline[:,2]<=deadline)),
                'selected_ontime_share':float(np.mean(chosen[:,2]<=deadline)),
                'mean_lateness_minutes':float(np.mean(np.maximum(chosen[:,2]-deadline,0)))})
        else:row.update({'outcomes_available':False,'observed_netgain':None,'selected_ontime_share':None,'nominal_ontime_share':None})
        rows.append(row)
    changed=[r for r in rows if r['shift']!=0];known=[r for r in rows if r['outcomes_available']]
    changed_known=[r for r in changed if r['outcomes_available']]
    mean=lambda values:float(np.mean(values))if values else None
    selected_ontime=mean([r['selected_ontime_share']for r in changed_known])
    nominal_ontime=mean([r['nominal_ontime_share']for r in changed_known])
    netgain=mean([r['observed_netgain']for r in known]);days=len({r['day']for r in changed})
    gates={'changed_at_least10':len(changed)>=10,'changed_days_at_least5':days>=5,
        'changed_outcomes_complete':len(changed_known)==len(changed),
        'full_schedule_outcomes_complete':len(known)==len(rows),
        'selected_ontime_at_least90pct':selected_ontime is not None and selected_ontime>=.9,
        'not_worse_than_nominal_by_more_than2pp':selected_ontime is not None and nominal_ontime is not None and selected_ontime-nominal_ontime>=-.02,
        'full_schedule_mean_netgain_positive':netgain is not None and netgain>0}
    from collections import Counter
    summary={'scheduled_issues':len(rows),'changed_issues':len(changed),'changed_distinct_days':days,
        'known_outcomes':len(known),'unknown_outcomes':len(rows)-len(known),
        'mean_observed_netgain_full_schedule':netgain,'changed_mean_selected_ontime_share':selected_ontime,
        'changed_mean_nominal_ontime_share':nominal_ontime,'gates':gates,'eligible':all(gates.values()),
        'status_counts':dict(Counter(r['status']for r in rows)),
        'fallback_issues':sum(r['fallback_applied']for r in rows),
        'fallback_mean_lateness':mean([r['mean_lateness_minutes']for r in known if r['fallback_applied']]),
        'full_schedule_mean_ontime_share':mean([r['selected_ontime_share']for r in known]),
        'changed_without_positive_observed_netgain':sum(r['observed_netgain']<=0 for r in changed_known),
        'changed_with_observed_ontime_below90pct':sum(r['selected_ontime_share']<.9 for r in changed_known),
        'claim':'Between-cohort retrospective comparison;not causal savings or independent trip-days.'}
    return {'summary':summary,'issues':rows}


def bootstrap_netgain(issues,repetitions=1000,seed=42):
    if repetitions!=1000 or seed!=42:raise ValueError('fixed whole-day bootstrap only')
    days=sorted({r['day']for r in issues});means=[]
    for day in days:
        rows=[r for r in issues if r['day']==day]
        if len(rows)!=12 or any(r.get('observed_netgain')is None for r in rows):
            return {'status':'unavailable_incomplete_full_day_outcomes','interval':None,'supports_positive_gain':False}
        means.append(np.mean([r['observed_netgain']for r in rows]))
    if not means:return {'status':'unavailable_empty','interval':None,'supports_positive_gain':False}
    values=np.asarray(means);rng=np.random.default_rng(seed)
    sampled=values[rng.integers(0,len(values),size=(repetitions,len(values)))].mean(axis=1)
    interval=np.quantile(sampled,[.025,.975]).tolist()
    return {'status':'descriptive_exposed_day_cluster_bootstrap','distinct_days':len(days),'repetitions':1000,'seed':42,
        'interval':interval,'supports_positive_gain':interval[0]>0,'not_independent_holdout':True}


def select_methods(common_scores,eligibility):
    order=('HGB','TCN_ensemble','empirical','current_proxy')
    admitted=[m for m in order if common_scores.get(m)is not None and np.isfinite(common_scores[m])]
    ranked=sorted(admitted,key=lambda m:(common_scores[m],order.index(m)))
    qualified=[m for m in ranked if eligibility.get(m,False)]
    return {'forecast_display_method':ranked[0]if ranked else None,
        'policy_selected_method':qualified[0]if qualified else None,'ranked_methods':ranked,
        'display_does_not_grant_policy_qualification':True}
