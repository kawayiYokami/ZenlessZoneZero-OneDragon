from __future__ import annotations

from typing import TYPE_CHECKING

from one_dragon.base.controller.drag_timing import DragTiming
from one_dragon.base.geometry.point import Point
from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.utils.log_utils import log
from zzz_od.application.bagel.bagel_item_vision import (
    ACTION_FILL,
    ACTION_SWAP,
    ITEM_TYPE_CURRENCY,
    BagelSlotMark,
    choose_store_action,
    identify_quality,
    identify_type,
    inspect_occupied,
)
from zzz_od.application.bagel.bagel_operation import BagelOperation
from zzz_od.application.bagel.bagel_search_panel import SearchPanelGuard
from zzz_od.application.bagel.bagel_slots import (
    RESULT_SLOT_CENTERS,
    SAFE_SLOT_CENTERS,
    SLOT_ITEM,
    SLOT_LOADING,
    inspect_safe_slots,
    slot_crop,
    slot_occupied,
    slot_states,
    swap_visually_ok,
    transfer_visually_ok,
)

if TYPE_CHECKING:
    from cv2.typing import MatLike

    from one_dragon.base.operation.operation_round_result import OperationRoundResult
    from zzz_od.context.zzz_context import ZContext

_DEST_CENTERS: dict[str, tuple[Point, ...]] = {
    ACTION_FILL: SAFE_SLOT_CENTERS,
    ACTION_SWAP: SAFE_SLOT_CENTERS,
}
_DEST_LABEL: dict[str, str] = {
    ACTION_FILL: '安全箱',
    ACTION_SWAP: '安全箱对换',
}
# 源格物品搬不动时返回的状态。节点边声明和返回值共用同一个常量：两处写错的字符串
# 不会报错，只会让框架找不到下一个节点、把「跳过这一格」静默当成整步结束。
_STATUS_SKIP_SLOT: str = '跳过不入箱的格子'


class BagelStoreSafe(BagelOperation):
    """将金币以外的结果装入安全箱，完成后保留面板供独立关闭步骤处理。"""

    STATUS_EMPTY: str = '无可入箱物品'
    STATUS_DONE: str = '已装入安全箱'
    # 保留物品搬运的前后台时序，不依赖公共拖拽默认值。
    FOREGROUND_DRAG_TIMING: DragTiming = DragTiming(
        press_wait=0.3, move_duration=0.3, release_wait=0.2,
    )
    BACKGROUND_DRAG_TIMING: DragTiming = DragTiming(
        press_wait=0.02, move_duration=0.6, release_wait=0.2,
    )
    # 拖拽后画面无变化时的重拖上限。搜索出高价值物品时游戏会先播一段加载动画，
    # 画面更新滞后于点击，一次重试不够；失败三次就把这一格记为不可入箱并跳过，
    # 不停机（金币本来就不进安全箱，别的物品也可能被游戏拒绝搬运）。
    DRAG_REPEAT_LIMIT: int = 3
    # 连续这么多件物品搬不动就不再往下磨：报局内流程失败，交给正式应用退出本局、
    # 结算后重开。一件搬不动只跳过该格；连续两件都搬不动，多半是这一局的面板或
    # 交互已经不对，继续磨只会把整局时间耗光。
    CONSECUTIVE_FAILURE_LIMIT: int = 2
    # 连续等不到稳定画面的帧数上限（每帧等待 0.3 秒，即约 3 秒）。
    # 面板刚打开、以及拖拽后物品落地都有动画，留足时间；每次发出拖拽前会清零，
    # 所以这是「一件物品一次」的预算，不会跨件累计。
    PANEL_WAIT_ROUNDS_LIMIT: int = 10
    # 没有可拖动候选、却还有格子处于「加载中」时，最多等这么多帧（约 3 秒）。
    # 等待只在连续加载期间计数，某一帧没有加载中格子就清零：揭示动画是一次性的，
    # 清零后再出现说明游戏又在揭示新的格子，应该重新给足时间；而某个格子一直判成
    # 加载中（例如暗色图标始终不过 0x28）时预算不会回涨，不会拖长本步。
    LOADING_ROUNDS_LIMIT: int = 10

    def __init__(self, ctx: ZContext, blueprint_priority: bool = True) -> None:
        """按传入的优先级收集当前容器，不关闭面板，也不补做交互。"""
        super().__init__(ctx, op_name='贝果-装入安全箱', timeout_seconds=90)
        self.blueprint_priority: bool = blueprint_priority
        self.moved: int = 0
        self.acted: bool = False
        self._pending_before: MatLike | None = None
        self._pending_source: Point | None = None
        self._pending_source_index: int | None = None
        self._pending_destination: Point | None = None
        self._pending_kind: str | None = None
        self._swap_seen: set[int] = set()
        self._unstorable: set[int] = set()
        self._consecutive_failures: int = 0
        self._drag_repeated: int = 0
        self._left_grid_waited: bool = False
        self._search_frame: MatLike | None = None
        self._panel_guard: SearchPanelGuard = SearchPanelGuard()
        self._panel_wait_rounds: int = 0
        self._safe_unknown_rounds: int = 0
        self._loading_rounds: int = 0

    def handle_init(self) -> None:
        """每次执行清空已装入计数与待核对拖拽。"""
        super().handle_init()
        self.moved = 0
        self.acted = False
        self._pending_before = None
        self._pending_source = None
        self._pending_source_index = None
        self._pending_destination = None
        self._pending_kind = None
        self._swap_seen = set()
        self._unstorable = set()
        self._consecutive_failures = 0
        self._drag_repeated = 0
        self._left_grid_waited = False
        self._search_frame = None
        self._panel_guard.reset()
        self._panel_wait_rounds = 0
        self._safe_unknown_rounds = 0
        self._loading_rounds = 0

    def _check_search_panel(self) -> OperationRoundResult | None:
        """搜查和拖后回读共用门槛；中断优先，未知画面有界等待。

        `_panel_wait_rounds` 是「连续等不到稳定画面」的帧数，**每次发出拖拽
        前都会清零**（见 `store_next`）。之前它只在守卫通过时清零，于是开局
        等面板铺开的几帧会被一直记着，拖完一件后再等几帧就累积越过上限，
        在画面完全正常的情况下误报「搜查面板状态持续不明」。
        """
        if self.is_bagel_result():
            self._clear_pending()
            return self.round_fail(self.STATUS_DEFEATED)
        if not self._search_ready():
            self._panel_guard.reset()
            if (not self._has_search_title()
                    and not self.round_by_find_area(
                        self.last_screenshot, '贝果-局内', '大保险解锁提示',
                    ).is_success
                    and self.round_by_find_area(
                        self.last_screenshot, '战斗画面', '按键-普通攻击',
                    ).is_success):
                self._clear_pending()
                return self.round_fail(self.STATUS_INTERRUPTED)
        elif self._panel_guard.observe(self.last_screenshot, self.last_screenshot_time):
            self._panel_wait_rounds = 0
            return None
        self._search_frame = None
        self._panel_wait_rounds += 1
        if self._panel_wait_rounds < self.PANEL_WAIT_ROUNDS_LIMIT:
            return self.round_wait('等待搜查面板到位并稳定', wait=0.3)
        self._clear_pending()
        return self._give_up('搜查面板状态持续不明')

    def _wait_for_loading(self, states: list[str | None]) -> OperationRoundResult | None:
        """还有格子处于加载中就先等一帧；没有或预算用完返回 None。

        只在要下结论之前调用：判空、以及安全箱满了没得换这两处收尾。拖动本身
        不等，可拖动的格子立刻拖。
        """
        loading = [index for index, state in enumerate(states) if state == SLOT_LOADING]
        if not loading:
            self._loading_rounds = 0
            return None
        if self._loading_rounds >= self.LOADING_ROUNDS_LIMIT:
            return None
        self._loading_rounds += 1
        return self.round_wait('等待搜查结果加载完成', wait=0.3)

    def _wait_for_safe_slots(self) -> OperationRoundResult:
        """未知格最多额外复查三帧；等待期间保留拖拽基线，不发送输入。"""
        self._safe_unknown_rounds += 1
        if self._safe_unknown_rounds <= 3:
            return self.round_wait('安全箱格子暂不明确，等待下一帧', wait=0.3)
        self._clear_pending()
        return self._give_up('安全箱格子状态不明')

    def _search_ready(self) -> bool:
        """搜查面板与安全箱同时可见才允许拖拽；武备箱与电子保险箱标题均可。"""
        title_ok = (
            self.round_by_find_area(
                self.last_screenshot, '贝果-局内', '搜查容器标题',
            ).is_success
            or self.round_by_find_area(
                self.last_screenshot, '贝果-局内', '电子保险箱标题',
            ).is_success
        )
        return title_ok and self.round_by_find_area(
            self.last_screenshot, '贝果-局内', '搜查安全箱',
        ).is_success and (self._search_complete() or self.round_by_find_area(
            self.last_screenshot, '贝果-局内', '搜查进行中',
        ).is_success)

    def _search_complete(self) -> bool:
        """只有搜索结果文字出现后才允许关闭面板。"""
        return self.round_by_find_area(
            self.last_screenshot, '贝果-局内', '搜查完成',
        ).is_success

    def _stable_results(self, results: list[BagelSlotMark]) -> list[BagelSlotMark]:
        """搜查进行中只处理连续两帧外观稳定的已揭示格。"""
        previous = self._search_frame
        current = self.last_screenshot
        self._search_frame = current.copy()
        if previous is None:
            return []
        stable: list[BagelSlotMark] = []
        for mark in results:
            before = slot_crop(previous, mark.center)
            after = slot_crop(current, mark.center)
            if (before is not None and after is not None and before.shape == after.shape
                    and slot_occupied(previous, mark.center)
                    and float(abs(before.astype('float32') - after.astype('float32')).mean()) < 4):
                stable.append(mark)
        return stable

    @node_from(from_name='核对入箱变化', status=_STATUS_SKIP_SLOT)
    @node_from(from_name='核对入箱变化', status='继续装入')
    @operation_node(name='逐件装入安全箱', is_start_node=True)
    def store_next(self) -> OperationRoundResult:
        """每次只拖一件并回读；装不下的留在搜索结果中。"""
        panel_result = self._check_search_panel()
        if panel_result is not None:
            return panel_result
        screen = self.last_screenshot
        states = slot_states(screen, RESULT_SLOT_CENTERS)
        # 三态按格各自判断：有可拖动的格子就立刻拖，不必等其它格子揭示完。
        # 但两处收尾（判空、安全箱满了没得换）之前要先确认没有格子还在加载，
        # 否则会把还没揭示完的格子当成「没东西可拿」草率结束。等待有上限，
        # 避免某个格子一直判成加载中拖长本步。
        # 金币不入安全箱：拾取后直接进携带货币、不占安全箱格子，所以从候选里直接去掉，
        # 它连拖都不会被拖；识别不稳（角标还没画出来）时由拖拽前复核兜底，见 _recheck_source。
        results = [
            mark for mark in inspect_occupied(
                screen,
                RESULT_SLOT_CENTERS,
                [index for index, state in enumerate(states) if state == SLOT_ITEM],
            )
            if mark.index not in self._unstorable
            and mark.item_type != ITEM_TYPE_CURRENCY
        ]
        if not self._search_complete():
            results = self._stable_results(results)
        if not results:
            loading_wait = self._wait_for_loading(states)
            if loading_wait is not None:
                return loading_wait
            if not self._search_complete():
                return self.round_wait('等待新的搜查结果', wait=0.3)
            if inspect_safe_slots(screen) is None:
                return self._wait_for_safe_slots()
            self._safe_unknown_rounds = 0
            status = self.STATUS_DONE if self.moved or self.acted else self.STATUS_EMPTY
            return self.round_success(status, data={'moved': self.moved})
        safe = inspect_safe_slots(screen)
        if safe is None:
            return self._wait_for_safe_slots()
        self._safe_unknown_rounds = 0
        choice = choose_store_action(
            results,
            [mark for mark in inspect_occupied(screen, SAFE_SLOT_CENTERS) if mark.index in safe.occupied],
            safe.empty,
            self.blueprint_priority,
        )
        if choice is None:
            # 安全箱满了、现有候选又都不比箱内的好，不等于没东西可拿：还有格子
            # 在揭示时，下一帧可能冒出更好的物品。先等揭示完再收工。
            loading_wait = self._wait_for_loading(states)
            if loading_wait is not None:
                return loading_wait
            if not self._search_complete():
                return self.round_wait('等待新的搜查结果', wait=0.3)
            return self.round_success(self.STATUS_DONE, data={'moved': self.moved})
        if choice.kind == ACTION_SWAP:
            if not self._search_complete():
                return self.round_wait('等待搜索完成后核对对换', wait=0.3)
            if choice.source_index in self._swap_seen:
                log.info(
                    '贝果入箱：结果格 %s 已对换，不再将换出的物品换回',
                    choice.source_index + 1,
                )
                return self.round_success(self.STATUS_DONE, data={'moved': self.moved})
            self._swap_seen.add(choice.source_index)
        source = RESULT_SLOT_CENTERS[choice.source_index]
        destination = _DEST_CENTERS[choice.kind][choice.dest_index]
        # 真正发出拖拽前用新一帧复核源格。卡片角标在面板揭示动画期间可能还没画出来，
        # 那时金币会被读成「其他」，而「其他」在优先级里并不低（非材料 + 品质高），
        # 于是金币会被选中；游戏又不接受金币入安全箱，拖多少次画面都不会变。
        # 复核通过后，拖前基线改用这一帧，比选择用的那一帧更贴近拖拽时刻。
        recheck = self._recheck_source(choice.source_index)
        if recheck is not None:
            return recheck
        self._pending_before = self.last_screenshot.copy()
        self._pending_source = source
        self._pending_source_index = choice.source_index
        self._pending_destination = destination
        self._pending_kind = choice.kind
        self._drag_repeated = 0
        self._left_grid_waited = False
        log.info(
            '贝果入箱：%s %s 结果格 %s -> %s格 %s',
            choice.mark.quality,
            choice.mark.item_type,
            choice.source_index + 1,
            _DEST_LABEL[choice.kind],
            choice.dest_index + 1,
        )
        self._drag_item(source, destination)
        return self.round_success('已拖拽一件', wait=0.7)

    def _recheck_source(self, source_index: int) -> OperationRoundResult | None:
        """拖拽前用新一帧复核源格；金币和已经不在的物品都不发拖拽。

        复核只决定「这一格还要不要拖」，不重新排序：画面在这一帧里变了就说明原选择
        已经过期，重新挑一次比拿着旧选择硬拖安全。返回 None 表示可以按原选择拖。
        """
        self.screenshot()
        source = RESULT_SLOT_CENTERS[source_index]
        if not slot_occupied(self.last_screenshot, source):
            return self.round_wait('源格物品已不在，重新挑选', wait=0.2)
        if identify_type(self.last_screenshot, source) != ITEM_TYPE_CURRENCY:
            return None
        give_up = self._skip_source(source_index, '金币不占安全箱')
        if give_up is not None:
            return give_up
        # 挑选就在本节点里，跳过之后留在本节点重新挑；返回带状态的成功会让框架
        # 去找「逐件装入安全箱」出发的边，找不到就当成整步结束，剩下的物品全丢。
        return self.round_wait('金币不占安全箱，改选其它物品', wait=0.2)

    def _skip_source(self, source_index: int, reason: str) -> OperationRoundResult | None:
        """记下搬不动的源格并跳过，不因此中断整局。

        一件搬不动就停机，代价远大于把它留在搜索结果里：金币本来就不进安全箱，
        其它物品也可能被游戏拒绝搬运。跳过之后继续装剩下的物品，全部跳完时由
        逐件装入安全箱按正常完成收尾。

        当场复核到金币的不算失败（金币不占安全箱，本来就不该搬），也不计入连续
        失败；其它原因才算，连续失败到上限就返回 _give_up 的结果退出本局重开。
        返回 None 表示只是跳过这一格，怎么继续由调用方决定（见 `_recheck_source`
        与 `核对入箱变化`）。
        """
        self._unstorable.add(source_index)
        source = RESULT_SLOT_CENTERS[source_index]
        observed_type = identify_type(self.last_screenshot, source)
        log.info(
            '贝果入箱：结果格 %s 当场复核为 %s %s，%s；跳过该格继续',
            source_index + 1,
            identify_quality(self.last_screenshot, source),
            observed_type,
            reason,
        )
        self._clear_pending()
        if observed_type == ITEM_TYPE_CURRENCY:
            return None
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.CONSECUTIVE_FAILURE_LIMIT:
            return self._give_up(
                f'连续 {self._consecutive_failures} 件物品搬不动（{reason}），退出本局重开',
            )
        return None

    def _skip_and_reselect(self, source_index: int, reason: str) -> OperationRoundResult:
        """核对节点的跳过：回到挑选节点继续装别的物品。

        待核对数据已经清掉，留在本节点只会报「缺少入箱前后对照数据」，所以必须
        带回挑选节点的状态；节点边用同一个常量声明，见 `_STATUS_SKIP_SLOT`。
        """
        give_up = self._skip_source(source_index, reason)
        if give_up is not None:
            return give_up
        return self.round_success(_STATUS_SKIP_SLOT, wait=0.2)

    def _give_up(self, reason: str) -> OperationRoundResult:
        """本步无法安全继续：报局内流程失败，由正式应用退出本局、结算后重开。

        返回的状态必须是 `贝果局内流程失败`：正式应用只对它有「退出本局再重开」的
        分支，其它自造状态会一路走到任务结束。原因放进 data，日志和现场截图都带上。
        退出时游戏只保留安全箱物资，比把整个任务停在这里更划算；整体重试次数照常扣。
        """
        return self.round_fail(self.STATUS_ROUND_FAILED, data=reason)

    def _clear_pending(self) -> None:
        """丢掉这一次拖拽的前后对照。"""
        self._pending_before = None
        self._pending_source = None
        self._pending_source_index = None
        self._pending_destination = None
        self._pending_kind = None

    @node_from(from_name='逐件装入安全箱', status='已拖拽一件')
    @operation_node(name='核对入箱变化')
    def confirm_transfer(self) -> OperationRoundResult:
        """按填空或对换核对画面变化。源格还在就再拖一次；离开搜索格却没进安全箱则另说。"""
        panel_result = self._check_search_panel()
        if panel_result is not None:
            return panel_result
        before = self._pending_before
        source = self._pending_source
        source_index = self._pending_source_index
        destination = self._pending_destination
        kind = self._pending_kind
        if (before is None or source is None or source_index is None
                or destination is None or kind is None):
            return self._give_up('缺少入箱前后对照数据')
        safe = inspect_safe_slots(self.last_screenshot)
        if safe is None:
            return self._wait_for_safe_slots()
        self._safe_unknown_rounds = 0
        if self._drag_visually_ok(kind, before, source, destination):
            self._clear_pending()
            self._drag_repeated = 0
            self._left_grid_waited = False
            self._consecutive_failures = 0
            if kind == ACTION_FILL:
                self.moved += 1
            self.acted = True
            return self.round_success('继续装入', wait=0.2)
        after = self.last_screenshot
        source_now = slot_occupied(after, source)
        dest_now = slot_occupied(after, destination)
        if (
            kind == ACTION_FILL
            and source_now
            and not dest_now
            and not slot_occupied(before, destination)
        ):
            # 源格还在、目标格仍空，且拖前目标格确实是空的：这次拖拽确实没生效。
            # 物品加载慢时画面滞后于点击，多给几次机会而不是一次就放弃。
            # 重试期间目标格保持不变，避免把物品拖到计划外位置。
            if self._drag_repeated < self.DRAG_REPEAT_LIMIT:
                self._drag_repeated += 1
                self._pending_before = after.copy()
                self._drag_item(source, destination)
                return self.round_wait(
                    f'入箱画面无变化，第 {self._drag_repeated} 次重拖', wait=0.7,
                )
            # 重拖预算用完画面仍无变化：这一格游戏就是不让搬（金币直接进携带货币、
            # 不占安全箱格；别的物品也可能被拒绝），再拖也是一样的结果。跳过它继续装
            # 剩下的物品，而不是停掉整局。
            return self._skip_and_reselect(
                source_index,
                f'首次拖拽和 {self.DRAG_REPEAT_LIMIT} 次重拖后源格仍未入箱',
            )
        if kind == ACTION_FILL and dest_now and not slot_occupied(before, destination):
            # 面板已通过稳定检查，目标格由空变有即可确认填空成功。
            # 源格可能误识别为占用，不能因此对已占用的目标再次拖拽。
            self._clear_pending()
            self._drag_repeated = 0
            self._left_grid_waited = False
            self._consecutive_failures = 0
            self.moved += 1
            self.acted = True
            return self.round_success('继续装入', wait=0.2)
        if (
            kind == ACTION_FILL
            and slot_occupied(before, source)
            and not source_now
            and not dest_now
        ):
            if not self._left_grid_waited:
                self._left_grid_waited = True
                return self.round_wait('离开了搜索格，再看一帧', wait=0.4)
            self._clear_pending()
            return self._give_up('离开了搜索格但没进安全箱')
        if kind == ACTION_SWAP and source_now and self._drag_repeated < 1:
            self._drag_repeated += 1
            self._pending_before = after.copy()
            self._drag_item(source, destination)
            return self.round_wait('对换画面未确认，再拖一次', wait=0.7)
        if kind == ACTION_SWAP:
            # 对换也确认不了：这一格换不进安全箱。跳过它去看别的候选，不停机。
            return self._skip_and_reselect(source_index, '对换重拖后两格外观仍未变化')
        self._clear_pending()
        return self._give_up('入箱画面无变化，源格和目标格都没动')

    def _drag_item(self, source: Point, destination: Point) -> None:
        """先按住再移动，给游戏时间抓起物品，避免拖成结果列表滚动。"""
        self._panel_guard.reset()
        # 每次发出拖拽都重新给一次「等面板稳住」的预算：开局等面板铺开、
        # 拖后等物品落地是两段独立动画，不能把前者的帧数算进后者的上限。
        self._panel_wait_rounds = 0
        timing = (
            self.BACKGROUND_DRAG_TIMING
            if self.ctx.controller.background_mode
            else self.FOREGROUND_DRAG_TIMING
        )
        self.ctx.controller.drag_to(
            end=destination, start=source, timing=timing,
        )

    def _drag_visually_ok(
        self,
        kind: str,
        before: MatLike,
        source: Point,
        destination: Point,
    ) -> bool:
        """填空看目标格空变有；对换看两边都占用且外观都变。"""
        after = self.last_screenshot
        if kind == ACTION_SWAP:
            return swap_visually_ok(before, after, source, destination)
        return transfer_visually_ok(before, after, source, destination)

    def _has_search_title(self) -> bool:
        """武备箱或电子保险箱标题任一可见即仍在搜查面板。"""
        return (
            self.round_by_find_area(
                self.last_screenshot, '贝果-局内', '搜查容器标题',
            ).is_success
            or self.round_by_find_area(
                self.last_screenshot, '贝果-局内', '电子保险箱标题',
            ).is_success
        )
