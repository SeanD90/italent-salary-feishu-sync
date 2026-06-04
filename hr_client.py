"""
HR 系统客户端 (北森 iTalent)
策略：导航 → 薪资档案 → 全部薪资档案标签
      表格是 FixedDataTable，用 [role=row] + cellContent 结构化读取
      按表头映射字段，翻页读完所有页
确认的选择器（通过实际页面 DOM 验证）：
  - 标签:   .out_view_item / .out_view_item_last  (innerText 匹配)
  - 行:     [role="row"]
  - 单元格: .public_fixedDataTableCell_cellContent
  - 翻页:   a.paging__btn__next  (禁用时 class 含 notAllowed)
"""
import asyncio
import re
from typing import Optional
from playwright.async_api import async_playwright, BrowserContext, Page, Frame

from config import HR_LOGIN_URL, HR_USERNAME, HR_PASSWORD
from logger import get_logger

log = get_logger("hr_client")


class HRClient:

    def __init__(self):
        self._playwright = None
        self._browser    = None
        self._context: Optional[BrowserContext] = None
        self._page:    Optional[Page]           = None
        self._frame:   Optional[Frame]          = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 3000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()
        log.info("浏览器已启动")

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            log.info("浏览器已关闭")
        except Exception as e:
            log.warning(f"关闭时出错: {e}")

    # ──────────────────────────────────────────────
    # JS 点击（exact 文字匹配，className 安全处理）
    # ──────────────────────────────────────────────
    async def _js_click(self, text: str, exclude_left: bool = False) -> bool:
        result = await self._page.evaluate(f"""
            () => {{
                const matches = Array.from(document.querySelectorAll('*')).filter(e => {{
                    if (e.children.length > 0) return false;
                    if ((e.innerText || '').trim() !== {text!r}) return false;
                    const r = e.getBoundingClientRect();
                    if (!r.width || !r.height) return false;
                    {'if (r.left < 150) return false;' if exclude_left else ''}
                    return true;
                }});
                if (!matches.length) return false;
                let el = matches[0];
                for (let i = 0; i < 6; i++) {{
                    if (!el) break;
                    const t = el.tagName;
                    const c = (typeof el.className === 'string') ? el.className : '';
                    if (t==='BUTTON'||t==='A'||el.onclick||
                        c.includes('btn')||c.includes('button')||
                        c.includes('item')||c.includes('app')||
                        c.includes('menu')||c.includes('link')||c.includes('tab')) {{
                        el.click(); return true;
                    }}
                    el = el.parentElement;
                }}
                matches[0].click(); return true;
            }}
        """)
        log.info(f"点击「{text}」: {'✅' if result else '❌'}")
        return bool(result)

    # ──────────────────────────────────────────────
    # 登录
    # ──────────────────────────────────────────────
    async def login(self) -> bool:
        log.info(f"登录: {HR_USERNAME}")
        try:
            await self._page.goto(HR_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            await self._page.fill('input[name="account"]', HR_USERNAME)
            await asyncio.sleep(0.8)
            await self._page.fill('input[name="password"]', HR_PASSWORD)
            await asyncio.sleep(0.8)
            try:
                cb = self._page.locator('.phoenix-checkbox').first
                if await cb.is_visible(timeout=2000):
                    cls = await cb.get_attribute("class") or ""
                    if "checked" not in cls:
                        await cb.click()
                        await asyncio.sleep(0.3)
            except Exception:
                pass
            await self._page.press('input[name="password"]', 'Enter')
            await self._page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(3)
            if "Login" in self._page.url:
                log.error("登录失败")
                return False
            log.info(f"登录成功: {self._page.url[:80]}")
            return True
        except Exception as e:
            log.error(f"登录异常: {e}", exc_info=True)
            return False

    # ──────────────────────────────────────────────
    # 导航到薪资档案 → 全部薪资档案
    # ──────────────────────────────────────────────
    async def _navigate_to_salary_page(self):
        log.info("导航: 导航台 → 薪酬社保 → 薪资档案")
        await self._js_click("导航台")
        await asyncio.sleep(3)
        await self._js_click("薪酬社保", exclude_left=True)
        await asyncio.sleep(4)
        await self._js_click("薪资档案")
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(8)

        # 找薪资 frame（含 role=row 且有工号）
        self._frame = await self._find_salary_frame()
        
        # 强制展开 FixedDataTable 高度，确保所有行都能渲染
        await self._expand_table()

        # 点「全部薪资档案」标签
        await self._click_all_archive_tab()

        # 轮询等待 URL 切换到 AllSalaryProfile（最多 15 秒）
        log.info("等待 URL 切换到 AllSalaryProfileView...")
        for _ in range(30):
            await asyncio.sleep(0.5)
            all_frames_urls = [f.url for f in self._page.frames]
            if any("AllSalaryProfile" in u for u in all_frames_urls):
                log.info("全部档案视图: ✅ (URL 已切换)")
                break
        else:
            log.warning("全部档案视图: ❓ (15秒内 URL 未切换，继续尝试读取)")

        await asyncio.sleep(3)
        # 重新定位 frame（URL 变化后 frame 可能换了）
        self._frame = await self._find_salary_frame()
        log.info(f"薪资 frame: {self._frame.url[:80]}")
        # 展开表格
        await self._expand_table()

    async def _find_salary_frame(self) -> Frame:
        """找到含薪资表格的 frame"""
        for f in self._page.frames:
            try:
                cnt = await f.evaluate("""
                    () => document.querySelectorAll('[role="row"]').length
                """)
                if cnt and cnt > 0:
                    txt = await f.evaluate("() => document.body.innerText.includes('工号')")
                    if txt:
                        return f
            except Exception:
                continue
        return self._page.main_frame

    async def _expand_table(self):
        """
        强制把 FixedDataTable 容器高度拉大，让它渲染所有行（不只渲染可见行）
        解决 headless 模式下只渲染10行而不是15行的问题
        """
        try:
            await self._frame.evaluate("""
                () => {
                    // 把 FixedDataTable 所有相关容器高度设为 5000px
                    const selectors = [
                        '.fixedDataTableLayout_main',
                        '.fixedDataTableLayout_rowsContainer',
                        '.public_fixedDataTable_main',
                        '.fixedDataTableLayout_body',
                        '[class*="fixedDataTable"]'
                    ];
                    let expanded = 0;
                    selectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => {
                            el.style.height = '5000px';
                            el.style.maxHeight = '5000px';
                            expanded++;
                        });
                    });
                    // 触发 resize 让 React 重新计算
                    window.dispatchEvent(new Event('resize'));
                    return expanded;
                }
            """)
            await asyncio.sleep(2)  # 等 React 重新渲染
        except Exception as e:
            log.debug(f"展开表格失败（不影响读取）: {e}")

    async def _click_all_archive_tab(self):
        """
        点「全部薪资档案」标签。
        该 SPA 需要真实鼠标事件（mouseover+click 完整链），
        JS .click() 无法触发视图切换，必须用 page.mouse.click()。
        """
        # 步骤1：在 frame 内查找 Tab 的屏幕坐标
        coords = await self._frame.evaluate("""
            () => {
                const tab = Array.from(
                    document.querySelectorAll('.out_view_item, .out_view_item_last')
                ).find(e =>
                    (e.innerText || '').trim() === '全部薪资档案' &&
                    e.getBoundingClientRect().width > 0
                );
                if (!tab) return null;
                const r = tab.getBoundingClientRect();
                return { x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2 };
            }
        """)

        if not coords:
            # 备选：在主 page 里找（有些 Tab 在主 frame 而非 iframe）
            coords = await self._page.evaluate("""
                () => {
                    const tab = Array.from(
                        document.querySelectorAll('.out_view_item, .out_view_item_last, [class*="view_item"]')
                    ).find(e =>
                        (e.innerText || '').trim() === '全部薪资档案' &&
                        e.getBoundingClientRect().width > 0
                    );
                    if (!tab) return null;
                    const r = tab.getBoundingClientRect();
                    return { x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2 };
                }
            """)

        if coords:
            x, y = coords['x'], coords['y']
            log.info(f"找到「全部薪资档案」Tab 坐标: ({x:.0f}, {y:.0f})")
            # 真实鼠标事件：移动 → 点击
            await self._page.mouse.move(x, y)
            await asyncio.sleep(0.3)
            await self._page.mouse.click(x, y)
            log.info("点击「全部薪资档案」标签(mouse): ✅")
        else:
            log.error("找不到「全部薪资档案」Tab 坐标，尝试 JS 兜底")
            # JS 兜底（大概率失败，但记录日志便于排查）
            await self._frame.evaluate("""
                () => {
                    const tab = Array.from(
                        document.querySelectorAll('.out_view_item, .out_view_item_last')
                    ).find(e => (e.innerText || '').trim() === '全部薪资档案');
                    if (tab) tab.click();
                }
            """)

    # ──────────────────────────────────────────────
    # 读取当前页（FixedDataTable 结构化）
    # ──────────────────────────────────────────────
    async def _read_current_page(self) -> list[dict]:
        """
        读取当前页所有行，返回 [{列名: 值}, ...]
        第一行是表头，后续是数据行
        """
        rows = await self._frame.evaluate("""
            () => {
                const result = [];
                document.querySelectorAll('[role="row"]').forEach(r => {
                    const cells = Array.from(r.querySelectorAll('.public_fixedDataTableCell_cellContent'))
                        .map(c => {
                            // 姓名列含头像占位符（无照片时显示姓名缩写），
                            // 优先读 .staff-name 拿干净姓名，排除头像文字
                            const nameEl = c.querySelector('.staff-name');
                            if (nameEl) {
                                const t = (nameEl.innerText || '').trim();
                                if (t) return t;
                            }
                            return (c.innerText || '').trim();
                        });
                    if (cells.length > 3) result.push(cells);
                });
                return result;
            }
        """)
        if not rows:
            return []

        # 第一个含"工号"的行是表头
        header = None
        for row in rows:
            if "工号" in row and "员工" in row:
                header = row
                break
        if not header:
            log.warning("未找到表头行")
            return []

        log.info(f"  表头: {[h for h in header if h]}")

        # 数据行：含6位工号的行
        records = []
        for row in rows:
            if row == header:
                continue
            # 找该行是否有工号
            has_emp = any(re.match(r'^\d{6}$', c) for c in row)
            if not has_emp:
                continue
            # 按表头位置映射
            rec = {}
            for i, col in enumerate(header):
                if col and i < len(row):
                    rec[col] = row[i]
            records.append(rec)
        return records

    # ──────────────────────────────────────────────
    # 翻页
    # ──────────────────────────────────────────────
    async def _read_current_page_stable(self) -> list[dict]:
        """
        稳定化读取：连续3次行数不增加才认为稳定，避免 FixedDataTable 渐进渲染漏行
        """
        best = []
        stable_count = 0
        for attempt in range(12):
            recs = await self._read_current_page()
            if len(recs) > len(best):
                best = recs
                stable_count = 0
            else:
                stable_count += 1
            # 连续3次不增加，且至少尝试4次 → 认为稳定
            if stable_count >= 3 and attempt >= 3:
                break
            await asyncio.sleep(1.0)
        log.info(f"  稳定读取完成: {len(best)} 行 (尝试 {attempt+1} 次)")
        return best

    async def _click_next_and_wait(self, prev_first_emp: str) -> bool:
        """
        点下一页，轮询等待首行工号变化（确认新页加载），再额外等待渲染完成
        """
        clicked = await self._frame.evaluate("""
            () => {
                const btn = Array.from(document.querySelectorAll('a')).find(a => {
                    const c = (typeof a.className==='string') ? a.className : '';
                    return c.includes('paging__btn__next') && (a.innerText||'').trim()==='下一页';
                });
                if (!btn) return 'notfound';
                const c = (typeof btn.className==='string') ? btn.className : '';
                if (c.includes('notAllowed')) return 'disabled';
                btn.click();
                return 'clicked';
            }
        """)
        if clicked != "clicked":
            log.info(f"翻页停止: {clicked}")
            return False

        # 轮询等待首行工号变化（最多15秒）
        for _ in range(30):
            await asyncio.sleep(0.5)
            recs = await self._read_current_page()
            if recs:
                cur_first = recs[0].get("工号")
                if cur_first and cur_first != prev_first_emp:
                    log.info(f"新页首行: {cur_first}")
                    # 确认页面切换后，额外等 3 秒让 FixedDataTable 渲染所有行
                    await asyncio.sleep(3)
                    return True
        log.warning("翻页后首行未变化，可能已到最后一页")
        return False

    # ──────────────────────────────────────────────
    # 字段映射
    # ──────────────────────────────────────────────
    def _num(self, val) -> Optional[float]:
        if not val or val in ("--", "－－", "—", ""):
            return None
        try:
            return float(str(val).replace(",", "").strip())
        except (ValueError, TypeError):
            return None

    def _map_to_fields(self, raw_rows: list[dict]) -> list[dict]:
        """映射字段，不在此去重（由 sync 层按工号+生效日期比对防重）"""
        results = []
        for row in raw_rows:
            emp_no = row.get("工号", "")
            if not emp_no or not re.match(r'^\d{6}$', emp_no):
                continue

            # 绩效标准：月度绩效基数优先，否则季度绩效基数÷3
            perf = self._num(row.get("月度绩效基数"))
            if perf is None:
                q = self._num(row.get("季度绩效基数"))
                if q is not None:
                    perf = round(q / 3, 2)

            # 奖金基数：月度奖金基数优先，否则季度奖金基数÷3
            bonus = self._num(row.get("月度奖金基数"))
            if bonus is None:
                q = self._num(row.get("季度奖金基数"))
                if q is not None:
                    bonus = round(q / 3, 2)

            # 固定工资：
            #   薪资包含「日薪」(实习生日薪) → 实习生日薪 × 22
            #   其他(含实习生月薪、正式员工) → 基本工资
            salary_pkg = row.get("薪资包", "") or ""
            daily = self._num(row.get("实习生日薪"))
            if "日薪" in salary_pkg and daily is not None:
                fixed_salary = round(daily * 22, 2)
            else:
                fixed_salary = row.get("基本工资", "")

            emp = {
                "emp_no":   emp_no,
                "name":     row.get("员工", ""),
                "dept":     row.get("部门", ""),
                "position": row.get("职务", ""),
                "currency": row.get("offer币种", "") or row.get("币种", ""),
                "固定工资": fixed_salary,
                "绩效标准": perf if perf is not None else "",
                "奖金基数": bonus if bonus is not None else "",
                "薪资包":   salary_pkg,
                "生效日期": row.get("生效日期", ""),
                "变动原因": row.get("变动原因", ""),
            }
            results.append(emp)
            log.info(f"  {emp['name']}({emp_no}): 固定={emp['固定工资']} "
                     f"绩效={emp['绩效标准']} 奖金={emp['奖金基数']} "
                     f"薪资包={emp['薪资包']} 生效={emp['生效日期']} 变动原因={emp['变动原因']}")
        return results

    # ──────────────────────────────────────────────
    # 主方法
    # ──────────────────────────────────────────────
    async def fetch_all_salary_data(self) -> list[dict]:
        if not await self.login():
            raise Exception("HR 系统登录失败")

        await self._navigate_to_salary_page()
        await asyncio.sleep(2)

        all_raw = []
        page_num = 1
        prev_first_emp = None

        while True:
            log.info(f"── 读取第 {page_num} 页 ──")
            await asyncio.sleep(1.5)
            # 每页读取前先展开表格，确保 FixedDataTable 渲染所有行
            await self._expand_table()
            # 稳定化读取，确保虚拟渲染完成
            page_records = await self._read_current_page_stable()

            if not page_records:
                log.warning(f"第 {page_num} 页无数据")
                break

            first_emp = page_records[0].get("工号")
            all_raw.extend(page_records)
            log.info(f"第 {page_num} 页：本页 {len(page_records)} 行，累计 {len(all_raw)} 行")

            # 点下一页并等待新页加载完成
            has_next = await self._click_next_and_wait(first_emp)
            if not has_next:
                break
            prev_first_emp = first_emp
            page_num += 1
            if page_num > 30:
                break

        if not all_raw:
            raise Exception("员工列表为空")

        employees = self._map_to_fields(all_raw)
        log.info(f"共获取 {len(employees)} 条员工薪资记录")
        if employees:
            log.info(f"数据样本(第1名): {employees[0]}")
        return employees
