import json
import logging
from typing import Dict, Any, List
from app.github_client import GitHubClient
from app.vector_store import VectorStore
from app.embeddings import GeminiEmbeddings
from app.gemini_client import GeminiClient
from app.tools import MCPTools

logger = logging.getLogger(__name__)

class RepositoryAnalyzerAgent:
    def __init__(
        self, 
        github_client: GitHubClient, 
        vector_store: VectorStore, 
        embeddings: GeminiEmbeddings,
        gemini_client: GeminiClient
    ):
        self.github_client = github_client
        self.vector_store = vector_store
        self.embeddings = embeddings
        self.gemini_client = gemini_client
        self.mcp_tools = MCPTools(github_client, vector_store, embeddings)

    async def analyze(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Analyze the repository. Gather files, README, project structure, 
        and semantic code context to generate overview and workflow.
        """
        logger.info(f"RepositoryAnalyzerAgent: Starting analysis for {owner}/{repo}")

        # 1. Fetch README
        readme_content = ""
        tree = await self.github_client.get_repo_tree(owner, repo)
        readme_item = next((item for item in tree if item.get("path", "").lower() in ["readme.md", "readme"]), None)
        if readme_item:
            try:
                readme_content = await self.github_client.get_file_content(owner, repo, readme_item["sha"])
            except Exception as e:
                logger.warning(f"Could not fetch README content: {e}")

        # 2. Fetch project files list and package configs if they exist
        pkg_configs = []
        for item in tree:
            path = item.get("path", "")
            if path in ["package.json", "requirements.txt", "go.mod", "Cargo.toml", "pom.xml", "build.gradle"]:
                try:
                    content = await self.github_client.get_file_content(owner, repo, item["sha"])
                    # Truncate to first 100 lines if too large
                    lines = content.splitlines()[:100]
                    pkg_configs.append(f"File: {path}\nContent Snippet:\n" + "\n".join(lines))
                except Exception:
                    pass

        # 3. Get file structure tree (truncated list for summary context)
        structure = [item.get("path", "") for item in tree if item.get("type") == "blob"]
        structure_str = "\n".join(structure[:100]) # Limit list length to fit token limits
        if len(structure) > 100:
            structure_str += f"\n... and {len(structure) - 100} more files."

        # 4. Perform vector queries for architectural keywords
        queries = ["architecture main process flow", "database connection setup", "api entry point frontend backend"]
        semantic_chunks = []
        for q in queries:
            try:
                emb = self.embeddings.get_embedding(q, is_query=True)
                results = self.vector_store.query_similar_chunks(owner, repo, emb, n_results=2)
                for r in results:
                    semantic_chunks.append(f"File: {r['metadata'].get('file_path')}\nContent:\n{r['content']}")
            except Exception as e:
                logger.warning(f"Failed semantic query during analysis: {e}")

        # 5. Build context
        context_parts = []
        if readme_content:
            context_parts.append(f"### README.md content:\n{readme_content[:3000]}") # Truncate readme if huge
        if pkg_configs:
            context_parts.append("### Project Configuration Files:\n" + "\n\n".join(pkg_configs))
        context_parts.append(f"### Repository Files Structure:\n{structure_str}")
        if semantic_chunks:
            context_parts.append("### Relevant Code Chunks:\n" + "\n\n".join(semantic_chunks))
            
        context = "\n\n".join(context_parts)

        # 6. Prompt Gemini for overview and workflow JSON
        system_instruction = (
            "You are the Repository Analyzer Agent, a specialized AI that understands code repositories.\n"
            "Analyze the provided repository files structure, README, configuration, and code chunks to understand the project.\n"
            "Your output must be a valid, parseable JSON document matching the schema requested below. Do not output anything else but JSON. No markdown backticks, no wrap text."
        )

        prompt = f"""
You are analyzing the GitHub repository: {owner}/{repo}.
Here is the context collected from the repository:
{context}

Please generate the Project Overview and Project Workflow.
You MUST respond with a single, valid JSON object. Do not include markdown code block syntax (like ```json). Just start with {{ and end with }}.

Required JSON Schema:
{{
  "overview": {{
    "project_name": "Project Name (clean, readable title)",
    "description": "High-level summary of what the project does",
    "problem_solved": "What real-world problem or challenge does this project solve?",
    "technologies": ["List of core technologies/frameworks used"],
    "languages": ["Main programming languages used (e.g. Python, Javascript)"],
    "dependencies": ["Key dependencies/libraries installed"],
    "features": ["3 to 5 key features of the application"],
    "important_files": [
      {{
        "path": "file_path_1",
        "description": "Brief description of this file's role in the project"
      }}
    ],
    "explanation": "A short, beginner-friendly explanation of how the project functions end-to-end."
  }},
  "workflow": {{
    "diagram": "A visual text-based flow diagram representing the execution sequence. Format example: User -> Frontend -> API Gateway -> Backend -> Database -> Response",
    "starting_point": "Where does the application execution start? (e.g. main.py, index.html, server.js)",
    "execution_flow": "Step-by-step description of the execution flow when a user interacts with the project",
    "frontend_backend": "How do the frontend and backend communicate? (REST API, WebSockets, none?)",
    "api_flow": "Describe the API routing and endpoints if any are present",
    "database_interaction": "Describe how the database is connected, queried, and updated (or mention if no database is used)",
    "processing_steps": "What are the core processing/business logic steps?",
    "final_output": "What is the final result or output of a successful run?"
  }}
}}
"""
        response_text = await self.gemini_client.generate(system_instruction, prompt)
        
        analysis_data = self._extract_json(response_text)
        if analysis_data and "overview" in analysis_data and "workflow" in analysis_data:
            # Ensure overview and workflow have the required dictionary keys
            overview = analysis_data.get("overview", {})
            workflow = analysis_data.get("workflow", {})
            
            # Normalize fields to avoid frontend crashes
            overview.setdefault("project_name", repo)
            overview.setdefault("description", "Repository overview analysis.")
            overview.setdefault("problem_solved", "N/A")
            overview.setdefault("technologies", [])
            overview.setdefault("languages", [])
            overview.setdefault("dependencies", [])
            overview.setdefault("features", [])
            overview.setdefault("important_files", [])
            overview.setdefault("explanation", "")
            
            workflow.setdefault("diagram", "Workflow diagram not specified.")
            workflow.setdefault("starting_point", "N/A")
            workflow.setdefault("execution_flow", "N/A")
            workflow.setdefault("frontend_backend", "N/A")
            workflow.setdefault("api_flow", "N/A")
            workflow.setdefault("database_interaction", "N/A")
            workflow.setdefault("processing_steps", "N/A")
            workflow.setdefault("final_output", "N/A")
            
            # Cache the valid summary
            self.mcp_tools.save_project_summary(owner, repo, analysis_data)
            return analysis_data

        logger.warning(f"Could not parse LLM JSON response or LLM unavailable. Building heuristic repository analysis from structure and files.")
        analysis_data = self._build_heuristic_summary(owner, repo, tree, readme_content, pkg_configs)
        self.mcp_tools.save_project_summary(owner, repo, analysis_data)
        return analysis_data

    def _build_heuristic_summary(
        self, 
        owner: str, 
        repo: str, 
        tree: List[Dict[str, Any]], 
        readme_content: str, 
        pkg_configs: List[str]
    ) -> Dict[str, Any]:
        """Build a reliable heuristic analysis when LLM APIs are offline or unreachable."""
        import os
        import re

        # Extract description from README if available
        description = f"{repo} repository created by @{owner}."
        if readme_content:
            lines = [l.strip() for l in readme_content.splitlines() if l.strip() and not l.startswith('#')]
            if lines:
                description = lines[0][:250]

        # Detect languages and key technologies
        lang_map = {
            '.py': 'Python', '.js': 'JavaScript', '.jsx': 'React / JavaScript',
            '.ts': 'TypeScript', '.tsx': 'React / TypeScript', '.html': 'HTML',
            '.css': 'CSS', '.cpp': 'C++', '.c': 'C', '.h': 'C/C++ Header',
            '.java': 'Java', '.go': 'Go', '.rs': 'Rust', '.sql': 'SQL',
            '.sh': 'Shell', '.php': 'PHP'
        }
        detected_langs = set()
        important_files = []
        entry_point = "N/A"

        for item in tree:
            path = item.get("path", "")
            _, ext = os.path.splitext(path.lower())
            if ext in lang_map:
                detected_langs.add(lang_map[ext])
            
            # Check for starting/entry points
            filename = os.path.basename(path).lower()
            if filename in ["main.py", "app.py", "index.js", "index.html", "server.js", "main.go"]:
                entry_point = path

            # Important files
            if filename in ["readme.md", "package.json", "requirements.txt", "main.py", "app.py", "index.html", "playwright.config.ts"] or len(important_files) < 5:
                if item.get("type") == "blob":
                    important_files.append({
                        "path": path,
                        "description": f"Core component of {repo}"
                    })

        # Deduplicate important files
        seen_paths = set()
        clean_important_files = []
        for f in important_files:
            if f["path"] not in seen_paths:
                seen_paths.add(f["path"])
                clean_important_files.append(f)

        languages_list = sorted(list(detected_langs)) if detected_langs else ["General Code"]
        
        # Parse dependencies from package configs if possible
        dependencies = []
        for pkg in pkg_configs:
            if "requirements.txt" in pkg:
                matches = re.findall(r"^([a-zA-Z0-9_\-]+)", pkg, re.MULTILINE)
                dependencies.extend([m for m in matches if m.lower() not in ["file:", "content"]])
            elif "package.json" in pkg:
                matches = re.findall(r'"([a-zA-Z0-9@/_\-]+)":\s*"[\^~]?[0-9]', pkg)
                dependencies.extend(matches)

        technologies = languages_list.copy()
        if any("react" in d.lower() for d in dependencies) or any("react" in l.lower() for l in languages_list):
            technologies.append("React")
        if any("fastapi" in d.lower() for d in dependencies):
            technologies.append("FastAPI")

        return {
            "overview": {
                "project_name": repo,
                "description": description,
                "problem_solved": f"Provides codebase and functionality for {repo}.",
                "technologies": technologies[:8],
                "languages": languages_list,
                "dependencies": dependencies[:10],
                "features": [
                    f"Implementation based in {', '.join(languages_list)}",
                    f"Structured repository with {len(tree)} tracked assets",
                    "Modular application components"
                ],
                "important_files": clean_important_files[:5],
                "explanation": f"{repo} is a {', '.join(languages_list)} project maintained by @{owner}. Execution starts from {entry_point}."
            },
            "workflow": {
                "diagram": f"User -> [{entry_point}] -> Processing Pipeline -> Result",
                "starting_point": entry_point,
                "execution_flow": f"Interactions trigger {entry_point}, which coordinates the modules defined across the repository.",
                "frontend_backend": "Direct execution or local script processing",
                "api_flow": "Local program logic / scripts",
                "database_interaction": "Filesystem or local data storage",
                "processing_steps": f"Parses inputs and executes workflows defined in {languages_list[0] if languages_list else 'code'}.",
                "final_output": "Execution output or interactive user interface"
            }
        }

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract and parse JSON from model response text even if markdown-wrapped or preceded by text."""
        import re
        if not text:
            return None
            
        clean_text = text.strip()
        
        # 1. Try direct parse
        try:
            return json.loads(clean_text)
        except Exception:
            pass
            
        # 2. Try markdown code block regex
        code_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean_text)
        if code_block:
            try:
                return json.loads(code_block.group(1).strip())
            except Exception:
                pass
                
        # 3. Try finding outermost { ... }
        start_idx = clean_text.find('{')
        end_idx = clean_text.rfind('}')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_substr = clean_text[start_idx:end_idx+1]
            try:
                return json.loads(json_substr)
            except Exception:
                # 4. Clean trailing commas in lists or objects
                cleaned = re.sub(r',\s*([\]}])', r'\1', json_substr)
                try:
                    return json.loads(cleaned)
                except Exception:
                    pass

        return None
