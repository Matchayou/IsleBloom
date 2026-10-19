import React from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export default function MarkdownText({value}){
  return <div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{value||''}</Markdown></div>;
}
