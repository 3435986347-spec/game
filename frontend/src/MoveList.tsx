import { useEffect, useRef } from "react";
import type { LibraryMove, Side } from "./api";

interface MoveListProps {
  moves: LibraryMove[];
  firstMover: Side;
  /** 突出显示的着法下标（打谱时为当前这步，-1 为开局局面）；不传则突出最后一步 */
  currentIndex?: number;
  /** 传入时着法可以点击，参数为着法下标 */
  onSelect?: (index: number) => void;
  emptyText?: string;
}

/** 按回合排列的着法列表：每行一个回合，红方在左、黑方在右。 */
export default function MoveList({
  moves,
  firstMover,
  currentIndex,
  onSelect,
  emptyText = "还没有走棋。点击棋子，再点击落点。",
}: MoveListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const currentRef = useRef<HTMLElement | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const following = currentIndex === undefined;

  // 对弈时跟随最新一步；打谱时只在列表内部滚动到当前这步，不带动整个页面
  useEffect(() => {
    if (following) endRef.current?.scrollIntoView({ block: "nearest" });
  }, [following, moves.length]);
  useEffect(() => {
    const list = listRef.current;
    const el = currentRef.current;
    if (following || !list) return;
    if (!el) {
      list.scrollTop = 0;
    } else if (el.offsetTop < list.scrollTop) {
      list.scrollTop = el.offsetTop;
    } else if (el.offsetTop + el.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = el.offsetTop + el.offsetHeight - list.clientHeight;
    }
  }, [following, currentIndex]);

  // 若从黑方先走的局面开始，第一回合红方位置留空
  const offset = firstMover === "black" ? 1 : 0;
  const cells: (LibraryMove | null)[] = offset ? [null, ...moves] : [...moves];
  const rows: (LibraryMove | null)[][] = [];
  for (let i = 0; i < cells.length; i += 2) rows.push(cells.slice(i, i + 2));
  const highlighted = (currentIndex ?? moves.length - 1) + offset;

  if (moves.length === 0) {
    return <p className="move-list-empty">{emptyText}</p>;
  }
  return (
    <div className={onSelect ? "move-list selectable" : "move-list"} ref={listRef}>
      <ol>
        {rows.map((row, r) => (
          <li key={r}>
            <span className="move-no">{r + 1}.</span>
            {[0, 1].map((c) => {
              const move = row[c];
              const index = r * 2 + c;
              const current = !!move && index === highlighted;
              const className = current ? "move current" : "move";
              if (move && onSelect) {
                return (
                  <button key={c} type="button" className={className} title={move.iccs}
                    ref={current ? (el) => { currentRef.current = el; } : undefined}
                    onClick={() => onSelect(index - offset)}>
                    {move.cn}
                  </button>
                );
              }
              return (
                <span key={c} className={className} title={move?.iccs}
                  ref={current ? (el) => { currentRef.current = el; } : undefined}>
                  {move ? move.cn : c === 0 ? "……" : ""}
                </span>
              );
            })}
          </li>
        ))}
      </ol>
      <div ref={endRef} />
    </div>
  );
}
