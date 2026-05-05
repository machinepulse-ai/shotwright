import { Fragment, ReactNode } from "react";

const BRAND_NAME = "Shotwright";

export function renderBrandText(value: string): ReactNode {
  if (!value.includes(BRAND_NAME)) {
    return value;
  }

  return value.split(BRAND_NAME).map((part, index, parts) => (
    <Fragment key={`${part}-${index}`}>
      {part}
      {index < parts.length - 1 ? (
        <span className="notranslate brand-name" translate="no">
          {BRAND_NAME}
        </span>
      ) : null}
    </Fragment>
  ));
}

