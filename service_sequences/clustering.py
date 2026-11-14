"""历程间混合距离与确定性 k-medoids 聚类。

距离由三个可解释分量加权组成：
- 序列分量：服务先后次序的 Levenshtein 编辑距离，按较长序列长度归一化；
- 节奏分量：相邻接触间隔分布的 Jensen-Shannon 散度；
- 结构分量：标准化后的结构特征欧氏距离（窗长、沉默、缺口、先验使用、重复上报等）。

k-medoids 采用经典 PAM 的 BUILD+SWAP 确定性实现，不依赖随机初始化；
medoid 是真实存在的户，因此每类的代表路径天然可核对、可命名。
"""

import math

from .features import STRUCTURE_ORDER, gap_histogram, structure_vector

# 分量权重是模型参数的一部分，随模型版本固定并写入制品。
DEFAULT_WEIGHTS = {"sequence": 0.4, "cadence": 0.3, "structure": 0.3}


def edit_distance(left, right):
    """服务序列的 Levenshtein 距离（仅插入/删除/替换，代价均为 1）。"""
    previous = list(range(len(right) + 1))
    for i, token in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            cost = 0 if token == other else 1
            current.append(min(
                previous[j] + 1,          # 删除
                current[j - 1] + 1,       # 插入
                previous[j - 1] + cost,   # 替换
            ))
        previous = current
    return previous[-1]


def jensen_shannon_divergence(left, right):
    """两个概率质量向量的 JS 散度，底数 2，取值 [0,1]。"""
    total = 0.0
    for a, b in zip(left, right):
        mixture = 0.5 * (a + b)
        if a > 0:
            total += a * math.log2(a / mixture)
        if b > 0:
            total += b * math.log2(b / mixture)
    return 0.5 * total


def fit_standardizer(journeys):
    """结构特征的稳健标准化参数：中位数与四分位距，缺失时回退到 1。"""
    columns = list(zip(*(structure_vector(journey) for journey in journeys)))
    scales = []
    for values in columns:
        ordered = sorted(values)
        median = _quantile_sorted(ordered, 0.5)
        q1 = _quantile_sorted(ordered, 0.25)
        q3 = _quantile_sorted(ordered, 0.75)
        spread = q3 - q1
        scales.append((median, spread if spread > 1e-9 else 1.0))
    return {"order": list(STRUCTURE_ORDER), "scales": scales}


def standardize(vector, standardizer):
    return [(value - median) / scale
            for value, (median, scale) in zip(vector, standardizer["scales"])]


class JourneyDistance:
    """预计算各户表示与标准化结构向量后的混合距离计算器。"""

    def __init__(self, journeys, standardizer, weights=None):
        self.journeys = journeys
        self.standardizer = standardizer
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self.sequences = [journey["service_sequence"] for journey in journeys]
        self.histograms = [gap_histogram(journey["gaps"]) for journey in journeys]
        self.structured = [
            standardize(structure_vector(journey), standardizer) for journey in journeys]
        self.size = len(journeys)

    def components(self, i, j):
        left, right = self.sequences[i], self.sequences[j]
        denom = max(1, len(left), len(right))
        sequence_part = edit_distance(left, right) / denom
        cadence_part = jensen_shannon_divergence(self.histograms[i], self.histograms[j])
        squared = sum((a - b) ** 2 for a, b in zip(self.structured[i], self.structured[j]))
        structure_part = math.sqrt(squared / len(STRUCTURE_ORDER))
        return {"sequence": sequence_part, "cadence": cadence_part,
                "structure": structure_part}

    def distance(self, i, j):
        if i == j:
            return 0.0
        parts = self.components(i, j)
        return sum(self.weights[key] * parts[key] for key in ("sequence", "cadence", "structure"))

    def explain(self, journey, nearest_index):
        """新户到训练集中某一户的距离分解，供接访页解释“为什么接近”。"""
        left = journey["service_sequence"]
        right = self.sequences[nearest_index]
        denom = max(1, len(left), len(right))
        sequence_part = edit_distance(left, right) / denom
        cadence_part = jensen_shannon_divergence(
            gap_histogram(journey["gaps"]), self.histograms[nearest_index])
        vector = standardize(structure_vector(journey), self.standardizer)
        squared = sum((a - b) ** 2 for a, b in zip(vector, self.structured[nearest_index]))
        structure_part = math.sqrt(squared / len(STRUCTURE_ORDER))
        parts = {"sequence": sequence_part, "cadence": cadence_part,
                 "structure": structure_part}
        return {"components": parts,
                "distance": sum(self.weights[key] * parts[key]
                                 for key in ("sequence", "cadence", "structure"))}


def pairwise_matrix(journeys, standardizer, weights=None):
    calculator = JourneyDistance(journeys, standardizer, weights)
    matrix = [[0.0] * calculator.size for _ in range(calculator.size)]
    for i in range(calculator.size):
        for j in range(i + 1, calculator.size):
            value = calculator.distance(i, j)
            matrix[i][j] = matrix[j][i] = value
    return matrix


def _quantile_sorted(ordered, q):
    if not ordered:
        return 0.0
    position = q * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _total_distance(matrix, medoid, members):
    return sum(matrix[index][medoid] for index in members)


def build_initial_medoids(matrix, k):
    """PAM BUILD 阶段：先选使总距离最小的户，再逐个选边际增益最大的户。"""
    n = len(matrix)
    first = min(range(n), key=lambda i: (sum(matrix[i]), i))
    medoids = [first]
    while len(medoids) < k:
        best_candidate, best_cost = None, None
        for candidate in range(n):
            if candidate in medoids:
                continue
            cost = 0.0
            for index in range(n):
                if index == candidate or index in medoids:
                    continue
                nearest = min(medoids, key=lambda medoid: matrix[index][medoid])
                cost += min(matrix[index][candidate], matrix[index][nearest])
            if best_cost is None or cost < best_cost or (cost == best_cost and candidate < best_candidate):
                best_candidate, best_cost = candidate, cost
        medoids.append(best_candidate)
    return medoids


def pam_swap(matrix, k, max_iterations=100):
    """确定性 k-medoids：BUILD 初值 + 类内 medoid 重选的交替优化。

    经典穷尽 SWAP 在每轮枚举全部 (medoid, 候选) 对，复杂度 O(n²k)；
    这里每轮把每个 medoid 更新为其类内总距离最小的真实成员，
    medoid 始终是真实户、结果仍完全确定，单轮复杂度 O(n²)。
    """
    n = len(matrix)
    if k > n:
        raise ValueError("类数不能多于户数")
    medoids = build_initial_medoids(matrix, k)
    for _ in range(max_iterations):
        assignments = _assign(matrix, medoids)
        updated = list(medoids)
        changed = False
        for cluster in range(k):
            members = [index for index, label in enumerate(assignments) if label == cluster]
            if not members:
                continue
            replacement = min(members, key=lambda index: (
                sum(matrix[index][other] for other in members), index))
            if replacement != updated[cluster]:
                updated[cluster] = replacement
                changed = True
        if not changed:
            break
        medoids = updated
    assignments = _assign(matrix, medoids)
    return medoids, assignments


def _assign(matrix, medoids):
    return [min(range(len(medoids)),
                key=lambda cluster: (matrix[index][medoids[cluster]], medoids[cluster], cluster))
            for index in range(len(matrix))]


def silhouette_score(matrix, assignments):
    """轮廓系数，空类或单类时返回 None 而不是伪造数值。"""
    n = len(matrix)
    labels = set(assignments)
    if len(labels) < 2:
        return None
    scores = []
    for index in range(n):
        own = assignments[index]
        same = [other for other in range(n) if assignments[other] == own]
        if len(same) <= 1:
            scores.append(0.0)
            continue
        intra = sum(matrix[index][other] for other in same if other != index) / (len(same) - 1)
        nearest_other = min(
            (sum(matrix[index][other] for other in range(n) if assignments[other] == label)
             / max(1, sum(1 for other in range(n) if assignments[other] == label))
             for label in labels if label != own))
        denom = max(intra, nearest_other)
        scores.append(0.0 if denom == 0 else (nearest_other - intra) / denom)
    return sum(scores) / len(scores)


def adjusted_rand_index(left, right):
    """ARI 实现，避免对 sklearn 标签重排行为的依赖。"""
    n = len(left)
    if n != len(right) or n < 2:
        return 1.0
    contingency = {}
    for a, b in zip(left, right):
        contingency[(a, b)] = contingency.get((a, b), 0) + 1

    def comb2(value):
        return value * (value - 1) / 2

    row_counts, col_counts = {}, {}
    for (a, b), count in contingency.items():
        row_counts[a] = row_counts.get(a, 0) + count
        col_counts[b] = col_counts.get(b, 0) + count
    sum_pairs = sum(comb2(count) for count in contingency.values())
    sum_rows = sum(comb2(count) for count in row_counts.values())
    sum_cols = sum(comb2(count) for count in col_counts.values())
    expected = sum_rows * sum_cols / comb2(n)
    maximum = 0.5 * (sum_rows + sum_cols)
    if maximum == expected:
        return 1.0
    return (sum_pairs - expected) / (maximum - expected)


def coassignment_rate(left, right):
    """两次划分中“同组/不同组”判断一致的户对比例，比 ARI 更直观。"""
    n = len(left)
    if n < 2:
        return 1.0
    agree, total = 0, 0
    for i in range(n):
        for j in range(i + 1, n):
            total += 1
            if (left[i] == left[j]) == (right[i] == right[j]):
                agree += 1
    return agree / total


def align_labels(reference, candidate):
    """用匈牙利式贪心把候选标签对齐到参考标签，对齐仅用于跨版本比较，不改写已发布名称。"""
    reference_labels = sorted(set(reference))
    candidate_labels = sorted(set(candidate))
    pairs = []
    for r_label in reference_labels:
        for c_label in candidate_labels:
            overlap = sum(1 for r, c in zip(reference, candidate)
                          if r == r_label and c == c_label)
            pairs.append((-overlap, r_label, c_label))
    mapping = {}
    used_references = set()
    for _, r_label, c_label in sorted(pairs):
        if c_label in mapping or r_label in used_references:
            continue
        mapping[c_label] = r_label
        used_references.add(r_label)
    leftover = [label for label in candidate_labels if label not in mapping]
    free = [label for label in reference_labels if label not in used_references]
    for c_label, r_label in zip(leftover, free):
        mapping[c_label] = r_label
    next_label = max(reference_labels, default=-1) + 1
    aligned = []
    for label in candidate:
        if label in mapping:
            aligned.append(mapping[label])
        else:
            mapping[label] = next_label
            aligned.append(next_label)
            next_label += 1
    return aligned
