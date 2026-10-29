/// <reference types="vite/client" />
import type React from "react";

declare global {
  interface File {
    webkitRelativePath?: string;
  }
}

declare module "react" {
  namespace JSX {
    interface IntrinsicAttributes {
      webkitdirectory?: string;
      directory?: string;
    }
  }
  interface InputHTMLAttributes<T extends HTMLElement>
    extends React.HTMLAttributes<T> {
    webkitdirectory?: string;
    directory?: string;
  }
}

export {};
