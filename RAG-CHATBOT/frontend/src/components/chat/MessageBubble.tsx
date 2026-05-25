"use client";

import { Message } from "@/lib/types";
// import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Copy } from "lucide-react";
// import { useState } from "react";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark as codeStyle } from "react-syntax-highlighter/dist/esm/styles/prism";

// Safe code block renderer without forwarding refs to avoid typing issues
type CodeProps = {
  inline?: boolean;
  className?: string;
  children?: React.ReactNode;
};

function CodeBlock({ inline, className, children }: CodeProps) {
  const match = /language-(\w+)/.exec(className || "");
  if (!inline && match) {
    return (
      <SyntaxHighlighter style={codeStyle} language={match[1]} PreTag="div">
        {String(children).replace(/\n$/, "")}
      </SyntaxHighlighter>
    );
  }
  return <code className={className}>{children}</code>;
}

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  // const [showSources, setShowSources] = useState<boolean>(false);

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied to clipboard!");
    } catch {
      toast.error("Failed to copy to clipboard");
    }
  };

  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4`}>
      <div className={`max-w-[80%] ${isUser ? "order-2" : "order-1"}`}>
        <div
          className={`rounded-lg p-4 ${
            isUser
              ? "bg-primary text-primary-foreground"
              : "bg-gray-100 border border-gray-200 text-gray-900"
          }`}
        >
          {/* Render markdown for assistant messages, plain text for user */}
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="prose prose-sm max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{ code: CodeBlock }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}

          {/* Citations Section removed as per requirements */}
        </div>

        {/* Message Footer: Only show copy button, no timestamp */}
        <div
          className={`flex items-center justify-end mt-2 text-xs text-muted-foreground ${
            isUser ? "flex-row-reverse" : "flex-row"
          }`}
        >
          <Button
            variant="ghost"
            size="sm"
            onClick={() => copyToClipboard(message.content)}
            className="h-6 w-6 p-0 opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <Copy className="h-3 w-3" />
          </Button>
        </div>
      </div>
    </div>
  );
}
