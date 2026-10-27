"""贝果每局收益、物品与时长统计：只写日志，不参与流程判断。

一局指从「零携带入场」到返回入口。收益取局内 HUD 背包图标旁的调查收益，
游戏只显示到 0.1K 一级，例如「198.5K」记为 198500。出售所得齿轮硬币另行
累计：出售会连同已有库存一起卖，不能摊到单局。

单件物品价值没有直接显示，按装箱时「本次调查收益」浮出的涨幅和总数差值推算，
见 ``BagelSafeLedger``。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from one_dragon.utils.log_utils import log

OUTCOME_SUCCESS: str = '成功入仓'
OUTCOME_EMPTY: str = '空箱'
OUTCOME_FAILED: str = '失败局'
OUTCOME_UNSUPPORTED: str = '非支持出生点'
OUTCOME_UNFINISHED: str = '未完成'

PHASE_ENTER: str = '入场'
PHASE_SPAWN: str = '出生识别'
PHASE_ROUTE: str = '路线'
PHASE_EXIT: str = '退出'
PHASE_SETTLE: str = '结算'
PHASE_RETURN: str = '返回'

_VALUE_PATTERN: re.Pattern[str] = re.compile(r'(\+?)(\d+(?:\.\d+)?)([KM]?)')
_VALUE_UNITS: dict[str, int] = {'': 1, 'K': 1_000, 'M': 1_000_000}


def _matches(text: str) -> list[tuple[bool, int]]:
    """按出现顺序列出（是否带加号, 换算后的整数）。"""
    compact = text.replace(',', '').replace(' ', '').upper()
    return [
        (sign == '+', round(float(number) * _VALUE_UNITS[unit]))
        for sign, number, unit in _VALUE_PATTERN.findall(compact)
    ]


def parse_value(text: str) -> int | None:
    """把「198.5K」「3476500」一类读数换成整数；读不出数字时返回 None。"""
    matches = _matches(text)
    return matches[0][1] if matches else None


def parse_survey(text: str) -> tuple[int | None, int | None]:
    """拆出面板底栏的总数与浮字涨幅。

    装箱时总数上方会短暂浮出「+31.5K」，按从上到下拼接后浮字在前。

    Args:
        text: 底栏区域 OCR 拼接后的文字。

    Returns:
        （总数, 浮字涨幅）；读不到的一项为 None。
    """
    matches = _matches(text)
    popup = next((value for plus, value in matches if plus), None)
    total = next((value for plus, value in reversed(matches) if not plus), None)
    return total, popup


def parse_quantity(text: str) -> int:
    """安全箱格子下方的数量；装备显示「Lv.50」之类等级，按 1 件计。"""
    compact = text.replace(' ', '')
    return int(compact) if compact.isdigit() else 1


def format_value(value: int | None) -> str:
    """按游戏习惯缩写成 K / M，未知时显示「未读到」。"""
    if value is None:
        return '未读到'
    if value >= 1_000_000:
        return f'{value / 1_000_000:.2f}M'
    if value >= 1_000:
        return f'{value / 1_000:.1f}K'
    return str(value)


@dataclass
class BagelItemRecord:
    """本局带出的一格物品。"""

    item_type: str
    quality: str
    quantity: int
    value: int | None
    """推算出的单格价值；推算不出时为 None。"""


@dataclass
class _Transfer:
    """一次尚未结算价值的装箱。"""

    swap: bool
    slot: int
    replaced: int | None
    """对换时被换出物品的价值；填空或价值未知时为 None。"""
    confirmed: bool = False
    popup: int | None = None
    """这次装箱浮出的涨幅；没读到为 None。"""


class BagelSafeLedger:
    """一局内安全箱各格物品的价值账本。

    每装一件，面板底栏「本次调查收益」上方浮出这件的涨幅（如「+31.5K」），
    总数随后滚动一到两秒才停。账本同时用两种读数：

    - 浮字：每张截图都读。出现新的浮字时，记到最近一次还没有浮字的装箱上。
      浮字一闪而过，可能漏读。
    - 停住的总数：连续两次读数相同才当作已停，与上次停住的总数相减，
      得到这段时间装进去的价值合计。合计减去已读到浮字的部分，
      若只剩一件没有浮字，余数就是这件的价值；剩两件以上记为未知，不硬拆。

    对换时按净涨幅加回被换出物品的价值；对换浮字是否等于净涨幅尚未实机确认。
    差值为 0 视为涨幅还没开始，留到下一次读数。关闭面板后 HUD 的读数视为
    已停，用来结算最后一件。安全箱每局从空开始，箱内每件的价值都是本局记下的。
    拖拽间隙若捡到金币，金币价值会算进没有浮字的那件物品。
    """

    def __init__(self) -> None:
        self.total: int | None = None
        """最近一次停住的调查收益。"""
        self.slot_values: dict[int, int | None] = {}
        self.slot_items: dict[int, tuple[str, str, int]] = {}
        """安全箱格号 -> （类型, 品质, 数量），取最后一次装箱结束时的画面。"""
        self._last_reading: int | None = None
        self._last_popup: int | None = None
        self._pending: list[_Transfer] = []

    def observe(self, reading: int | None, popup: int | None = None) -> None:
        """记录一次面板底栏读数；总数与上一次相同才结算。

        Args:
            reading: 本张截图读到的调查收益总数，读不到为 None。
            popup: 本张截图读到的浮字涨幅，没有为 None。
        """
        if popup is not None and popup != self._last_popup:
            target = next((transfer for transfer in reversed(self._pending) if transfer.popup is None), None)
            if target is not None:
                target.popup = popup
        self._last_popup = popup
        stable = reading is not None and reading == self._last_reading
        self._last_reading = reading
        if stable and reading is not None:
            self.settle(reading)

    def settle(self, total: int) -> None:
        """用一个已停住的总数结算待定的装箱。"""
        if any(not transfer.confirmed for transfer in self._pending):
            return
        if not self._pending:
            self.total = total
            return
        delta = None if self.total is None else total - self.total
        if delta == 0:
            return
        missing = [transfer for transfer in self._pending if transfer.popup is None]
        known = sum(transfer.popup for transfer in self._pending if transfer.popup is not None)
        for transfer in self._pending:
            if transfer.popup is not None:
                increase: int | None = transfer.popup
            elif delta is not None and len(missing) == 1:
                increase = delta - known
            else:
                increase = None
            value = increase
            if transfer.swap and increase is not None:
                value = None if transfer.replaced is None else increase + transfer.replaced
            self.slot_values[transfer.slot] = value if value is not None and value > 0 else None
        self._pending.clear()
        self.total = total

    def start_transfer(self, swap: bool, slot: int) -> None:
        """拖拽发出前登记；确认入箱前不参与结算。"""
        replaced = self.slot_values.get(slot) if swap else None
        self._pending.append(_Transfer(swap=swap, slot=slot, replaced=replaced))

    def confirm_transfer(self) -> None:
        """最近一次拖拽已确认入箱。"""
        for transfer in reversed(self._pending):
            if not transfer.confirmed:
                transfer.confirmed = True
                return

    def cancel_transfer(self) -> None:
        """最近一次拖拽没进安全箱，撤销登记。"""
        for index in range(len(self._pending) - 1, -1, -1):
            if not self._pending[index].confirmed:
                del self._pending[index]
                return

    def report(self) -> list[BagelItemRecord]:
        """按格号列出本局带出的物品和推算价值。"""
        return [
            BagelItemRecord(item_type, quality, quantity, self.slot_values.get(slot))
            for slot, (item_type, quality, quantity) in sorted(self.slot_items.items())
        ]


def format_items(items: list[BagelItemRecord]) -> str:
    """把物品列成「S 蓝图×1 ≈31.5K」一类文字。"""
    if not items:
        return '无'
    return '；'.join(
        f'{item.quality} {item.item_type}×{item.quantity} ≈{format_value(item.value)}'
        for item in items
    )


@dataclass
class _ItemTotal:
    """某一类型与品质的累计。"""

    slots: int = 0
    quantity: int = 0
    value: int = 0
    unknown: int = 0


@dataclass
class _Round:
    """一局的计时、收益与物品。"""

    attempt: int
    started: float
    phase: str | None = None
    phase_started: float = 0.0
    phases: dict[str, float] = field(default_factory=dict)
    income: int | None = None
    items: list[BagelItemRecord] = field(default_factory=list)
    outcome: str = OUTCOME_UNFINISHED


class BagelRunStats:
    """按局累计收益与各阶段耗时，每局结束打一行本局统计和一行累计统计。"""

    def __init__(self) -> None:
        self.task_started: float = time.monotonic()
        self.current: _Round | None = None
        self.outcome_counts: dict[str, int] = {}
        self.finished_rounds: int = 0
        self.completed_rounds: int = 0
        """有明确结果的局数；未完成的局不计入平均用时。"""
        self.completed_seconds: float = 0.0
        self.unsupported_seconds: float = 0.0
        self.total_income: int = 0
        self.income_rounds: int = 0
        """读到收益且结果为成功入仓的局数，用于算成功局平均收益。"""
        self.success_income: int = 0
        self.sale_count: int = 0
        self.sale_coins: int = 0
        self.sale_unknown: int = 0
        self.item_totals: dict[tuple[str, str], _ItemTotal] = {}

    def start_round(self, attempt: int) -> None:
        """开始新的一局；上一局没收尾时按未完成记账。

        Args:
            attempt: 本局是第几次入场。
        """
        if self.current is not None:
            self.finish_round()
        now = time.monotonic()
        self.current = _Round(attempt=attempt, started=now)
        self.begin_phase(PHASE_ENTER)

    def begin_phase(self, phase: str) -> None:
        """切换到某阶段；同一阶段重复调用不重新计时。"""
        current = self.current
        if current is None or current.phase == phase:
            return
        now = time.monotonic()
        self._close_phase(current, now)
        current.phase = phase
        current.phase_started = now

    def set_income(self, income: int | None) -> None:
        """记录本局调查收益；读不到时保持未知。"""
        if self.current is not None and income is not None:
            self.current.income = income

    def set_items(self, items: list[BagelItemRecord]) -> None:
        """记录本局安全箱带出的物品。"""
        if self.current is not None:
            self.current.items = list(items)

    def set_outcome(self, outcome: str) -> None:
        """记录本局结果。"""
        if self.current is not None:
            self.current.outcome = outcome

    def finish_round(self) -> None:
        """收尾当前局，输出本局与累计统计。"""
        current = self.current
        if current is None:
            return
        self.current = None
        now = time.monotonic()
        self._close_phase(current, now)
        seconds = now - current.started
        self.finished_rounds += 1
        if current.outcome != OUTCOME_UNFINISHED:
            self.completed_rounds += 1
            self.completed_seconds += seconds
        self.outcome_counts[current.outcome] = self.outcome_counts.get(current.outcome, 0) + 1
        if current.outcome == OUTCOME_UNSUPPORTED:
            self.unsupported_seconds += seconds
        if current.income is not None:
            self.total_income += current.income
            if current.outcome == OUTCOME_SUCCESS:
                self.income_rounds += 1
                self.success_income += current.income
        for item in current.items:
            total = self.item_totals.setdefault((item.quality, item.item_type), _ItemTotal())
            total.slots += 1
            total.quantity += item.quantity
            if item.value is None:
                total.unknown += 1
            else:
                total.value += item.value
        phases = '/'.join(f'{name}{value:.1f}s' for name, value in current.phases.items())
        log.info(
            '贝果本局统计：第 %s 次入场 · 结果=%s · 调查收益=%s · 用时 %s · 合计 %.1fs',
            current.attempt, current.outcome, format_value(current.income), phases or '无', seconds,
        )
        if current.items:
            log.info('贝果本局物品：%s', format_items(current.items))
        self._log_summary()

    def log_final(self) -> None:
        """任务结束时收尾未完成的局，再输出总表与物品汇总。"""
        if self.current is not None:
            self.finish_round()
        if not self.finished_rounds:
            return
        log.info('贝果任务统计总表：')
        self._log_summary()
        if not self.item_totals:
            log.info('贝果累计物品：无')
            return
        ordered = sorted(self.item_totals.items(), key=lambda pair: pair[1].value, reverse=True)
        for (quality, item_type), total in ordered:
            if total.unknown == total.slots:
                value = '价值未推算'
            elif total.unknown:
                value = f'价值约 {format_value(total.value)}（{total.unknown} 格未推算）'
            else:
                value = f'价值约 {format_value(total.value)}'
            log.info(
                '贝果累计物品：%s %s %s 格，共 %s 件，%s',
                quality, item_type, total.slots, total.quantity, value,
            )

    def record_sale(self, coins: int | None) -> None:
        """记录一次出售所得；读不到时只计次数。"""
        self.sale_count += 1
        if coins is None:
            self.sale_unknown += 1
            log.info('贝果出售统计：本次出售获得未读到；累计出售 %s 次', self.sale_count)
            return
        self.sale_coins += coins
        log.info(
            '贝果出售统计：本次获得 %s 齿轮硬币；累计出售 %s 次，共 %s（%s 次未读到）',
            coins, self.sale_count, self.sale_coins, self.sale_unknown,
        )

    def _close_phase(self, current: _Round, now: float) -> None:
        """把正在计时的阶段累加进本局。"""
        if current.phase is None:
            return
        elapsed = now - current.phase_started
        current.phases[current.phase] = current.phases.get(current.phase, 0.0) + elapsed
        current.phase = None

    def _log_summary(self) -> None:
        """输出累计统计。每小时收益按任务开始以来的实际时间计算。"""
        hours = (time.monotonic() - self.task_started) / 3600
        counts = '/'.join(
            f'{name}{self.outcome_counts.get(name, 0)}'
            for name in (OUTCOME_SUCCESS, OUTCOME_EMPTY, OUTCOME_FAILED, OUTCOME_UNSUPPORTED, OUTCOME_UNFINISHED)
            if self.outcome_counts.get(name, 0)
        )
        average_success = self.success_income // self.income_rounds if self.income_rounds else None
        per_hour = round(self.total_income / hours) if hours > 0 else None
        average_seconds = (
            f'{self.completed_seconds / self.completed_rounds:.1f}s' if self.completed_rounds else '无'
        )
        log.info(
            '贝果累计统计：%s 局（%s）· 调查收益合计 %s · 成功局平均 %s · 每小时 %s · '
            '平均每局 %s · 非支持出生点累计 %.1f 分钟 · 出售 %s 次共 %s 齿轮硬币',
            self.finished_rounds, counts, format_value(self.total_income), format_value(average_success),
            format_value(per_hour), average_seconds,
            self.unsupported_seconds / 60, self.sale_count, self.sale_coins,
        )
