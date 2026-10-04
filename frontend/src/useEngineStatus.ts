import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { EngineStatus } from "./api";

/** 象棋引擎状态：打开页面时检查一次，check() 可重新检查（如安装引擎之后）。 */
export function useEngineStatus() {
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [checkFailed, setCheckFailed] = useState(false);

  const check = useCallback(() => {
    setCheckFailed(false);
    api.engineStatus().then(setEngine, () => setCheckFailed(true));
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  return { engine, checkFailed, check };
}
