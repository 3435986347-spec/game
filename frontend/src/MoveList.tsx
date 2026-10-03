import { useEffect, useRef } from "react";
import type { MoveRecord, Side } from "./api";

interface MoveListProps {
  moves: MoveRecord[];
  firstMover: Side;
}

/** 按回合排列的着法列表：每行一个回合，红方在左、黑方在右。 */
export default function MoveList({ moves, firstMover }: MoveListProps) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [moves.length]);

  // 若从黑方先走的局面开始，第一回合红方位置留空
  const cells: (MoveRecord | null)[] = firstMover === "black" ? [null, ...moves] : [...moves];
  const rows: (MoveRecord | null)[][] = [];
  for (let i = 0; i < cells.length; i += 2) rows.push(cells.slice(i, i + 2));
  const lastIndex = moves.length - 1 + (firstMover === "black" ? 1 : 0);

  if (moves.length === 0) {
    return <p className="move-list-empty">还没有走棋。点击棋子，再点击落点。</p>;
  }
  return (
    <div className="move-list">
      <ol>
        {rows.map((row, r) => (
          <li key={r}>
            <span className="move-no">{r + 1}.</span>
            {[0, 1].map((c) => {
              const move = row[c];
              const index = r * 2 + c;
              return (
                <span key={c} className={index === lastIndex ? "move current" : "move"}
                  title={move?.iccs}>
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
