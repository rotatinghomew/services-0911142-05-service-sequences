"""分型学习流水线：类数选择、稳定性验证、原型描述与新户归类。

设计原则：
- 训练过程完全确定性（固定种子的 LCG 自助抽样），同一份资料与参数必然得到同一模型指纹；
- 类数选择同时报告轮廓系数（分离质量）与自助稳定性（ARI/共分配率），不把不稳定的划分包装成结论；
- 窗长敏感性把早期户截断到晚期登记组的观察窗，单独报告窗长效应，避免把“看得更短”误读为路径变化；
- 归类保留“不适合归类”：绝对距离或类间边际不足时拒识，由人工接访而非硬塞进最近的类。
"""

from .clustering import (
    DEFAULT_WEIGHTS, JourneyDistance, adjusted_rand_index, align_labels,
    coassignment_rate, fit_standardizer, pairwise_matrix, pam_swap, silhouette_score,
)
from .features import (
    GAP_BIN_EDGES, STRUCTURE_ORDER, extract_journeys, truncate_journeys,
)
from .storage import fingerprint

MIN_STABLE_ARI = 0.80
MIN_STABLE_COASSIGN = 0.85
# 拒识阈值：训练户到各自 medoid 距离的第 95 分位，以及最近与次近类的最小相对边际。
DISTANCE_PERCENTILE = 95
MIN_MARGIN_RATIO = 0.08
MIN_CLUSTER_SIZE = 3
DEFAULT_BOOTSTRAPS = 40
MAX_CANDIDATE_K = 8


class DeterministicSampler:
    """固定种子的线性同余抽样器，保证跨机器、跨重启的自助样本完全一致。"""

    def __init__(self, seed):
        self.state = seed & 0x7FFFFFFF or 1

    def integer(self, upper):
        # Numerical Recipes LCG 参数。
        self.state = (1664525 * self.state + 1013904223) & 0x7FFFFFFF
        return self.state % upper

    def resample_indices(self, size):
        return [self.integer(size) for _ in range(size)]


def choose_k(matrix, max_k=MAX_CANDIDATE_K, bootstraps=DEFAULT_BOOTSTRAPS, seed=20261114):
    """扫描可行类数，返回每个 k 的质量与稳定性指标以及推荐结果。"""
    n = len(matrix)
    upper = min(max_k, max(2, n // MIN_CLUSTER_SIZE))
    candidates = []
    for k in range(2, upper + 1):
        medoids, assignments = pam_swap(matrix, k)
        sizes = [assignments.count(label) for label in range(k)]
        silhouette = silhouette_score(matrix, assignments)
        stability = bootstrap_stability(matrix, k, bootstraps, seed + k)
        candidates.append({
            "k": k,
            "silhouette": silhouette,
            "smallest_cluster": min(sizes),
            "bootstrap_ari_mean": stability["ari_mean"],
            "bootstrap_ari_min": stability["ari_min"],
            "coassignment_mean": stability["coassignment_mean"],
            "stable": (stability["ari_mean"] >= MIN_STABLE_ARI
                       and stability["coassignment_mean"] >= MIN_STABLE_COASSIGN
                       and min(sizes) >= MIN_CLUSTER_SIZE),
        })
    stable_options = [candidate for candidate in candidates if candidate["stable"]]
    pool = stable_options or candidates
    recommended = max(pool, key=lambda item: (
        item["silhouette"] if item["silhouette"] is not None else -1.0,
        -item["k"],
    ))
    return {
        "candidates": candidates,
        "recommended_k": recommended["k"],
        "all_candidates_stable": bool(stable_options),
        "criterion": {
            "silhouette": "混合距离下的轮廓系数，衡量类间分离与类内紧凑",
            "bootstrap_ari": f"{bootstraps} 次固定种子自助重训的调整兰德指数，均值阈值 {MIN_STABLE_ARI}",
            "coassignment": f"户对共分配一致率，均值阈值 {MIN_STABLE_COASSIGN}",
            "min_cluster_size": MIN_CLUSTER_SIZE,
        },
    }


def bootstrap_stability(matrix, k, bootstraps, seed):
    """对自助子样本重训并与全量划分比较，报告 ARI 与共分配率分布。"""
    n = len(matrix)
    full_medoids, full_assignments = pam_swap(matrix, k)
    sampler = DeterministicSampler(seed)
    ari_values, co_values = [], []
    for _ in range(bootstraps):
        sample = sampler.resample_indices(n)
        unique_sample = sorted(set(sample))
        if len(unique_sample) < k + 1:
            continue
        sub_matrix = [[matrix[i][j] for j in unique_sample] for i in unique_sample]
        _, sub_assignments = pam_swap(sub_matrix, k)
        sub_by_original = {original: label for original, label
                           in zip(unique_sample, sub_assignments)}
        reference = [full_assignments[index] for index in unique_sample]
        candidate = [sub_by_original[index] for index in unique_sample]
        aligned = align_labels(reference, candidate)
        ari_values.append(adjusted_rand_index(reference, aligned))
        co_values.append(coassignment_rate(reference, aligned))
    if not ari_values:
        return {"ari_mean": 0.0, "ari_min": 0.0, "coassignment_mean": 0.0, "runs": 0}
    return {
        "ari_mean": sum(ari_values) / len(ari_values),
        "ari_min": min(ari_values),
        "coassignment_mean": sum(co_values) / len(co_values),
        "runs": len(ari_values),
    }


def window_sensitivity(data, standardizer, weights, full_medoids, full_assignments, seed=20261114):
    """窗长效应检验：把早期户截断到晚期登记组窗长后重训并对齐标签。"""
    late_windows = [row["stop"] - row["start"] for row in data["coverage"]
                    if next(h["cohort"] for h in data["households"]
                            if h["household"] == row["household"]) == "late"]
    if not late_windows:
        return {"available": False, "reason": "资料中没有晚期登记组，无法构造对照窗"}
    cutoff = min(late_windows)
    truncated = truncate_journeys(data, cutoff)
    full_journeys = extract_journeys(data)
    full_lookup = {
        journey["household"]: label
        for journey, label in zip(full_journeys, full_assignments)
    }
    early_ids = [journey["household"] for journey in truncated
                 if journey["cohort"] == "early"]
    if len(truncated) < 4 or not early_ids:
        return {"available": False, "reason": "可截断的户数不足", "cutoff_days": cutoff}
    matrix = pairwise_matrix(truncated, standardizer, weights)
    k = len(full_medoids)
    _, truncated_assignments = pam_swap(matrix, k)
    reference = [full_lookup[journey["household"]] for journey in truncated]
    aligned = align_labels(reference, truncated_assignments)
    moved = [
        {"household": journey["household"],
         "published_cluster": full_lookup[journey["household"]],
         "truncated_cluster": label}
        for journey, label in zip(truncated, aligned)
        if label != full_lookup[journey["household"]]
    ]
    early_index = [i for i, journey in enumerate(truncated) if journey["cohort"] == "early"]
    early_reference = [reference[i] for i in early_index]
    early_aligned = [aligned[i] for i in early_index]
    return {
        "available": True,
        "cutoff_days": cutoff,
        "ari_all_households": adjusted_rand_index(reference, aligned),
        "coassignment_all_households": coassignment_rate(reference, aligned),
        "ari_early_households": adjusted_rand_index(early_reference, early_aligned),
        "moved_households": moved,
        "interpretation": "ARI 高表示跨窗划分一致；移动户应结合截断后剩余事件数人工复核，而非视为路径改变",
    }


def describe_cluster(journeys, assignments, label, calculator, medoid_index):
    """生成一个类的代表路径与组内差异描述。"""
    members = [i for i, value in enumerate(assignments) if value == label]
    medoid = journeys[medoid_index]
    distances = [calculator.distance(i, medoid_index) for i in members]
    event_counts = [len(journeys[i]["events"]) for i in members]
    windows = [journeys[i]["window_length"] for i in members]
    channels = {}
    cohorts = {}
    prior_count = 0
    duplicate_count = 0
    uncovered_count = 0
    for i in members:
        journey = journeys[i]
        channels[journey["channel"]] = channels.get(journey["channel"], 0) + 1
        cohorts[journey["cohort"]] = cohorts.get(journey["cohort"], 0) + 1
        prior_count += int(journey["structure"]["prior_history"])
        duplicate_count += journey["duplicate_count"]
        uncovered_count += 1 if journey["uncovered_days"] > 0 else 0
    gap_values = [gap for i in members for gap in journeys[i]["gaps"]]
    return {
        "cluster_id": label,
        "size": len(members),
        "medoid_household": medoid["household"],
        "representative_path": {
            "service_sequence": medoid["service_sequence"],
            "event_days": [event["day"] for event in medoid["events"]],
            "gaps_days": medoid["gaps"],
            "window_length": medoid["window_length"],
            "uncovered_days": medoid["uncovered_days"],
            "prior_history": bool(medoid["structure"]["prior_history"]),
        },
        "within_group_variation": {
            "distance_to_medoid_mean": _round(sum(distances) / len(distances)),
            "distance_to_medoid_max": _round(max(distances)),
            "event_count_min": min(event_counts),
            "event_count_max": max(event_counts),
            "window_length_min": min(windows),
            "window_length_max": max(windows),
            "gap_days_min": min(gap_values) if gap_values else None,
            "gap_days_max": max(gap_values) if gap_values else None,
            "channels": channels,
            "cohorts": cohorts,
            "prior_history_households": prior_count,
            "households_with_uncovered_gap": uncovered_count,
            "duplicate_reports": duplicate_count,
        },
        "member_households": [journeys[i]["household"] for i in members],
    }


def train_model(data, params=None):
    """完整训练：抽取历程、选类数、稳定性验证、窗长检验并组装不可变模型制品。"""
    params = dict(params or {})
    k = params.get("k")
    max_k = int(params.get("max_k", MAX_CANDIDATE_K))
    bootstraps = int(params.get("bootstraps", DEFAULT_BOOTSTRAPS))
    seed = int(params.get("seed", 20261114))
    weights = dict(params.get("weights") or DEFAULT_WEIGHTS)
    if bootstraps < 1 or bootstraps > 500 or seed < 0:
        raise ValueError("自助次数应为 1 至 500，种子应为非负整数")
    if set(weights) != {"sequence", "cadence", "structure"} or any(
            not isinstance(value, (int, float)) or value < 0 for value in weights.values()):
        raise ValueError("距离权重必须包含 sequence/cadence/structure 三个非负数")

    journeys = extract_journeys(data)
    if len(journeys) < 2 * MIN_CLUSTER_SIZE:
        raise ValueError("户数过少（少于 %d），不足以形成可复核的分型" % (2 * MIN_CLUSTER_SIZE))
    feasible_k = min(MAX_CANDIDATE_K, max(2, len(journeys) // MIN_CLUSTER_SIZE))
    if k is not None:
        if not isinstance(k, int) or not (2 <= k <= feasible_k):
            raise ValueError("指定类数超出可行范围：2 至 %d" % feasible_k)
    standardizer = fit_standardizer(journeys)
    matrix = pairwise_matrix(journeys, standardizer, weights)
    selection = choose_k(matrix, max_k=max_k, bootstraps=bootstraps, seed=seed)
    chosen_k = k or selection["recommended_k"]
    medoid_indices, assignments = pam_swap(matrix, chosen_k)
    calculator = JourneyDistance(journeys, standardizer, weights)
    clusters = [
        describe_cluster(journeys, assignments, label, calculator, medoid_indices[label])
        for label in range(chosen_k)
    ]
    sensitivity = window_sensitivity(
        data, standardizer, weights, medoid_indices, assignments, seed=seed)
    accepted_distances = sorted(
        calculator.distance(i, medoid_indices[assignments[i]]) for i in range(len(journeys)))
    reject_distance = _percentile_sorted(accepted_distances, DISTANCE_PERCENTILE)
    artifact = {
        "schema_version": 1,
        "training": {
            "dataset_fingerprint": fingerprint(data),
            "household_count": len(journeys),
            "params": {"k": chosen_k, "max_k": max_k, "bootstraps": bootstraps,
                       "seed": seed, "weights": weights},
            "k_selection": selection,
            "window_sensitivity": sensitivity,
        },
        "representation": {
            "structure_order": list(STRUCTURE_ORDER),
            "gap_bin_edges": list(GAP_BIN_EDGES),
            "standardizer": standardizer,
            "weights": weights,
            "long_gap_days_threshold": 21,
        },
        "clusters": clusters,
        "medoid_indices": medoid_indices,
        "assignment": [
            {"household": journey["household"], "cluster_id": label}
            for journey, label in zip(journeys, assignments)
        ],
        "reject_policy": {
            "distance_percentile": DISTANCE_PERCENTILE,
            "distance_threshold": reject_distance,
            "min_margin_ratio": MIN_MARGIN_RATIO,
        },
        "naming": {"version": 0, "names": {}},
    }
    artifact["model_fingerprint"] = fingerprint(_fingerprint_material(artifact))
    return artifact


def classify_journey(data, model, household=None):
    """把一户（默认取资料中全部户）放入已发布分型，支持拒识并给出相似依据。"""
    journeys = extract_journeys(data)
    if household is not None:
        journeys = [journey for journey in journeys if journey["household"] == household]
        if not journeys:
            raise LookupError("资料中找不到该户：" + str(household))
    training_journeys = model.get("_training_journeys")
    if training_journeys is None:
        training_journeys = extract_journeys(_require_dataset(model))
    calculator = JourneyDistance(training_journeys, model["representation"]["standardizer"],
                                 model["representation"]["weights"])
    medoid_indices = model["medoid_indices"]
    threshold = model["reject_policy"]["distance_threshold"]
    margin_ratio = model["reject_policy"]["min_margin_ratio"]
    results = []
    for journey in journeys:
        scored = []
        for label, medoid_index in enumerate(medoid_indices):
            explanation = calculator.explain(journey, medoid_index)
            scored.append((label, explanation["distance"], explanation["components"]))
        scored.sort(key=lambda item: (item[1], item[0]))
        best_label, best_distance, best_components = scored[0]
        second_distance = scored[1][1] if len(scored) > 1 else float("inf")
        margin = second_distance - best_distance
        reasons = [
            ("distance_threshold", best_distance > threshold,
             "到最近原型的距离 %.4f 超过训练户 95 分位阈值 %.4f" % (best_distance, threshold)),
            ("weak_margin", margin < margin_ratio * max(best_distance, 1e-9),
             "最近与次近类边际 %.4f 不足（要求至少为最近距离的 %.0f%%）"
             % (margin, margin_ratio * 100)),
        ]
        reject_reasons = [{"code": code, "detail": detail}
                          for code, triggered, detail in reasons if triggered]
        nearest_members = _nearest_members(calculator, journey, model, best_label)
        results.append({
            "household": journey["household"],
            "cluster_id": None if reject_reasons else best_label,
            "nearest_cluster_id": best_label,
            "distance_to_cluster": _round(best_distance),
            "distance_components": {key: _round(value) for key, value in best_components.items()},
            "second_cluster_id": scored[1][0] if len(scored) > 1 else None,
            "second_distance": _round(second_distance) if second_distance != float("inf") else None,
            "reject_reasons": reject_reasons,
            "status": "rejected" if reject_reasons else "assigned",
            "similar_households": nearest_members,
            "observed_events": len(journey["events"]),
            "window_length": journey["window_length"],
        })
    return results if household is None else results[0]


def _nearest_members(calculator, journey, model, best_label, limit=3):
    """返回同类内最接近的真实户及其距离分解，解释“为什么接近”。"""
    assignment_lookup = {item["household"]: item["cluster_id"] for item in model["assignment"]}
    member_indices = [
        index for index, row in enumerate(calculator.journeys)
        if assignment_lookup.get(row["household"]) == best_label
    ]
    scored = []
    for index in member_indices:
        explanation = calculator.explain(journey, index)
        scored.append((index, explanation["distance"], explanation["components"]))
    scored.sort(key=lambda item: (item[1], item[0]))
    return [
        {"household": calculator.journeys[index]["household"],
         "distance": _round(distance),
         "components": {key: _round(value) for key, value in components.items()}}
        for index, distance, components in scored[:limit]
    ]


def _require_dataset(model):
    dataset = model.get("_training_dataset")
    if dataset is None:
        raise LookupError("模型制品缺少训练资料，无法计算新户距离")
    return dataset


def with_dataset(model, data):
    """把训练资料挂到内存中的模型副本上；资料不写入模型指纹。"""
    enriched = dict(model)
    enriched["_training_dataset"] = data
    enriched["_training_journeys"] = extract_journeys(data)
    return enriched


def _fingerprint_material(artifact):
    """指纹只覆盖学习产物与参数，不含运行期挂载的资料或人工命名状态以外的易变字段。"""
    return {
        "schema_version": artifact["schema_version"],
        "training": artifact["training"],
        "representation": artifact["representation"],
        "clusters": artifact["clusters"],
        "medoid_indices": artifact["medoid_indices"],
        "assignment": artifact["assignment"],
        "reject_policy": artifact["reject_policy"],
    }


def _percentile_sorted(ordered, percentile):
    if not ordered:
        return 0.0
    position = (percentile / 100.0) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _round(value, digits=6):
    return None if value is None else round(float(value), digits)
