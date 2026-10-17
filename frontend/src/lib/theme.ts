/**
 * 主题：跟随系统 / 浅色 / 深色。
 *
 * 只负责「算 + 写」，DOM 上只动一个东西：`<html data-theme="light|dark">`。
 * CSS 那边只认这个属性，不写 `prefers-color-scheme` 媒体查询 —— 三档里只有
 * 「跟随系统」需要看系统偏好，在这里解析成具体值即可，样式表因此保持单一事实来源。
 *
 * ⚠️ `index.html` 里有一段等价的引导脚本，负责在**首帧之前**把 `data-theme` 写上，
 * 否则会先闪一下浅色。改这里的 STORAGE_KEY 记得同步改那边。
 */

export type ThemeChoice = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "wardrobe.theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

export function getThemeChoice(): ThemeChoice {
  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  return saved === "light" || saved === "dark" ? saved : "system";
}

function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia(DARK_QUERY).matches;
}

/** 把选择落到 DOM 上。返回实际生效的主题（system 会解析成 light / dark）。 */
export function applyTheme(choice: ThemeChoice): "light" | "dark" {
  const resolved = choice === "system" ? (systemPrefersDark() ? "dark" : "light") : choice;
  document.documentElement.dataset.theme = resolved;
  return resolved;
}

export function setThemeChoice(choice: ThemeChoice): "light" | "dark" {
  localStorage.setItem(THEME_STORAGE_KEY, choice);
  return applyTheme(choice);
}

/** 选了「跟随系统」时，系统切换要跟着变。返回取消订阅函数。 */
export function watchSystemTheme(onChange: () => void): () => void {
  const mq = window.matchMedia(DARK_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
