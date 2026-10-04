/** 焦点在需要键盘输入的控件上时（输入框、下拉框、单选框、滑块等），页面快捷键不触发。
 *  复选框和按钮不用方向键，不拦截——否则勾选“显示引擎分析”后方向键就不能翻棋谱了。 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
  if (!(target instanceof HTMLInputElement)) return false;
  return !["checkbox", "button", "submit", "reset"].includes(target.type);
}
