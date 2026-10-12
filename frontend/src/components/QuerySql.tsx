import { FC } from "react";

export const QuerySql: FC<{ sql: string }> = ({ sql }) => {
  return <pre className="sql">{sql}</pre>;
};
