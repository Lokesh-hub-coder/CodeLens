package com.codelensai.parser;

import com.codelensai.parser.model.CodeEntities;
import com.github.javaparser.JavaParser;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;
import com.github.javaparser.resolution.declarations.ResolvedMethodDeclaration;
import com.github.javaparser.symbolsolver.JavaSymbolSolver;
import com.github.javaparser.symbolsolver.javaparsermodel.declarations.JavaParserMethodDeclaration;
import com.github.javaparser.symbolsolver.resolution.typesolvers.CombinedTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.JavaParserTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.ReflectionTypeSolver;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * Extracts classes, methods, a call graph, and an import-based dependency
 * graph from a Java repository, mirroring exactly the four collections
 * app/parsing/java_bridge.py deserializes on the Python side.
 *
 * Directories skipped here intentionally mirror IGNORED_DIR_NAMES in
 * backend/app/parsing/repo_walker.py (.git, target, build, out, ...) --
 * keep the two lists in sync if either changes.
 */
public final class RepoParser {

    private static final Set<String> IGNORED_DIR_NAMES = Set.of(
            ".git", "target", "build", "out", "node_modules", ".idea", ".vscode"
    );

    private final Path repoRoot;
    private final JavaParser javaParser;

    public RepoParser(Path repoRoot) {
        this.repoRoot = repoRoot.toAbsolutePath().normalize();
        this.javaParser = new JavaParser(buildParserConfiguration(this.repoRoot));
    }

    /**
     * Configures JavaParser with a symbol solver combining the JDK
     * (reflection-based -- resolves java.* / javax.* calls so they can be
     * correctly identified as "not ours" and skipped, rather than
     * misreported as unresolved-and-therefore-suspicious) and the repo's
     * own source (so calls between the repo's own classes resolve to real
     * declarations). LanguageLevel is pinned high (JAVA_17) so newer
     * syntax parses; this does not prevent parsing older-style code.
     */
    private static ParserConfiguration buildParserConfiguration(Path repoRoot) {
        CombinedTypeSolver typeSolver = new CombinedTypeSolver();
        typeSolver.add(new ReflectionTypeSolver());
        typeSolver.add(new JavaParserTypeSolver(repoRoot));

        // Common Maven/Gradle layout: also register src/main/java explicitly
        // in case the repo root itself is not the source root the caller
        // passed in (harmless no-op if it doesn't exist or duplicates the
        // root already added above).
        Path conventionalSourceRoot = repoRoot.resolve("src/main/java");
        if (Files.isDirectory(conventionalSourceRoot)) {
            typeSolver.add(new JavaParserTypeSolver(conventionalSourceRoot));
        }

        ParserConfiguration config = new ParserConfiguration();
        config.setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_17);
        config.setSymbolResolver(new JavaSymbolSolver(typeSolver));
        return config;
    }

    public CodeEntities.ParseResult parse() throws IOException {
        List<Path> javaFiles = findJavaFiles();
        List<CompilationUnit> compilationUnits = new ArrayList<>();
        List<String> unparsedFiles = new ArrayList<>();

        // Pass 1: parse every file once. CompilationUnits are kept in
        // memory for pass 2 so nothing is re-parsed from disk.
        for (Path file : javaFiles) {
            com.github.javaparser.ParseResult<CompilationUnit> result = javaParser.parse(file);
            if (result.isSuccessful() && result.getResult().isPresent()) {
                compilationUnits.add(result.getResult().get());
            } else {
                unparsedFiles.add(repoRoot.relativize(file).toString().replace('\\', '/'));
            }
        }

        // Pass 2a: register every class -> ClassInfo, building the id
        // registry that pass 2b's import-edge resolution needs.
        CodeEntities.ParseResult output = new CodeEntities.ParseResult();
        output.unparsed_files = unparsedFiles;
        Map<String, CodeEntities.ClassInfo> classById = new LinkedHashMap<>();

        for (CompilationUnit cu : compilationUnits) {
            for (ClassOrInterfaceDeclaration decl : cu.findAll(ClassOrInterfaceDeclaration.class)) {
                CodeEntities.ClassInfo info = toClassInfo(cu, decl);
                if (info != null) {
                    classById.put(info.id, info);
                    output.classes.add(info);
                }
            }
        }

        // Pass 2b: methods, call edges, import edges. Requires classById
        // to already be complete (import edges only point at classes we
        // actually parsed -- see buildImportEdges).
        // ── Diagnostic accumulators ──────────────────────────────────────────
        int totalVisited = 0, totalResolved = 0, totalCallEdge = 0,
            totalExternal = 0, totalUnresolved = 0;
        // ────────────────────────────────────────────────────────────────────
        for (CompilationUnit cu : compilationUnits) {
            MethodAndCallVisitor visitor = new MethodAndCallVisitor(classById.keySet());
            visitor.visit(cu, null);
            output.methods.addAll(visitor.methods);
            output.call_edges.addAll(visitor.callEdges);
            output.import_edges.addAll(buildImportEdges(cu, classById));
            // ── Accumulate diagnostic counters ───────────────────────────────
            totalVisited    += visitor.diagTotal;
            totalResolved   += visitor.diagResolved;
            totalCallEdge   += visitor.diagCallEdge;
            totalExternal   += visitor.diagExternal;
            totalUnresolved += visitor.diagUnresolved;
            // ─────────────────────────────────────────────────────────────────
        }

        // ── Diagnostic summary to stderr (one line per category) ─────────────
        double resolvedRate  = totalVisited   > 0 ? 100.0 * totalResolved   / totalVisited   : 0;
        double callEdgeRate  = totalVisited   > 0 ? 100.0 * totalCallEdge   / totalVisited   : 0;
        double externalRate  = totalVisited   > 0 ? 100.0 * totalExternal   / totalVisited   : 0;
        double unresolvedRate = totalVisited  > 0 ? 100.0 * totalUnresolved / totalVisited   : 0;
        System.err.printf("[DIAG] MethodCallExpr visited : %d%n", totalVisited);
        System.err.printf("[DIAG] resolve() succeeded    : %d (%.1f%%)%n", totalResolved, resolvedRate);
        System.err.printf("[DIAG]   -> became CallEdge   : %d (%.1f%% of total) [in-repo calls]%n", totalCallEdge, callEdgeRate);
        System.err.printf("[DIAG]   -> external/skipped  : %d (%.1f%% of total) [JDK/library — correct]%n", totalExternal, externalRate);
        System.err.printf("[DIAG] resolve() threw        : %d (%.1f%%) [unresolvable — correct skip]%n", totalUnresolved, unresolvedRate);
        System.err.printf("[DIAG] Graph signal estimate  : %.1f%% of visited calls became graph edges%n", callEdgeRate);
        // ─────────────────────────────────────────────────────────────────────

        return output;
    }

    private List<Path> findJavaFiles() throws IOException {
        try (Stream<Path> walk = Files.walk(repoRoot)) {
            return walk
                    .filter(Files::isRegularFile)
                    .filter(p -> p.toString().endsWith(".java"))
                    .filter(p -> !isUnderIgnoredDir(p))
                    .collect(Collectors.toList());
        }
    }

    private boolean isUnderIgnoredDir(Path file) {
        for (Path part : repoRoot.relativize(file)) {
            if (IGNORED_DIR_NAMES.contains(part.toString())) {
                return true;
            }
        }
        return false;
    }

    private CodeEntities.ClassInfo toClassInfo(CompilationUnit cu, ClassOrInterfaceDeclaration decl) {
        if (decl.getBegin().isEmpty() || decl.getEnd().isEmpty()) {
            return null; // synthetic/generated node with no source position -- nothing useful to index
        }
        CodeEntities.ClassInfo info = new CodeEntities.ClassInfo();
        info.id = resolveQualifiedName(decl);
        info.name = decl.getNameAsString();
        info.packageName = cu.getPackageDeclaration().map(pd -> pd.getNameAsString()).orElse("");
        info.file_path = relativePath(cu);
        info.start_line = decl.getBegin().get().line;
        info.end_line = decl.getEnd().get().line;
        info.imports = cu.getImports().stream()
                .map(ImportDeclaration::getNameAsString)
                .collect(Collectors.toList());
        return info;
    }

    private List<CodeEntities.ImportEdge> buildImportEdges(
            CompilationUnit cu, Map<String, CodeEntities.ClassInfo> classById) {
        List<CodeEntities.ImportEdge> edges = new ArrayList<>();
        List<ClassOrInterfaceDeclaration> declsInFile = cu.findAll(ClassOrInterfaceDeclaration.class);
        if (declsInFile.isEmpty()) {
            return edges;
        }
        for (ImportDeclaration imp : cu.getImports()) {
            String importedName = imp.getNameAsString();
            // Only imports that resolve to a class *we parsed* become
            // dependency-graph edges -- imports of the JDK or third-party
            // libraries carry no retrievable source and would only add
            // dangling nodes (same philosophy as CallGraph.build on the
            // Python side skipping unresolved callees).
            if (!classById.containsKey(importedName)) {
                continue;
            }
            for (ClassOrInterfaceDeclaration fromDecl : declsInFile) {
                String fromId = resolveQualifiedName(fromDecl);
                if (!fromId.equals(importedName)) {
                    edges.add(new CodeEntities.ImportEdge(fromId, importedName));
                }
            }
        }
        return edges;
    }

    /** Prefer the symbol solver's canonical name (handles nested classes,
     * generics, etc. correctly); fall back to package+simple-name if
     * resolution fails for any reason -- a best-effort id is more useful
     * downstream than dropping the class entirely. */
    private String resolveQualifiedName(ClassOrInterfaceDeclaration decl) {
        try {
            return decl.resolve().getQualifiedName();
        } catch (Exception e) {
            String pkg = decl.findCompilationUnit()
                    .flatMap(CompilationUnit::getPackageDeclaration)
                    .map(pd -> pd.getNameAsString())
                    .orElse("");
            return pkg.isEmpty() ? decl.getNameAsString() : pkg + "." + decl.getNameAsString();
        }
    }

    private String relativePath(CompilationUnit cu) {
        return cu.getStorage()
                .map(storage -> repoRoot.relativize(storage.getPath()).toString().replace('\\', '/'))
                .orElse("UNKNOWN");
    }

    /** Builds "ClassId#name(Type1,Type2)" the same way regardless of
     * whether the MethodDeclaration is being registered directly or being
     * reached via a resolved call -- see the class-level note in
     * MethodAndCallVisitor.visit(MethodCallExpr, ...) for why this
     * consistency is what makes call-edge ids actually match method ids. */
    static String buildMethodId(String classId, MethodDeclaration decl) {
        return classId + "#" + buildSignatureLabel(decl);
    }

    static String buildSignatureLabel(MethodDeclaration decl) {
        String params = decl.getParameters().stream()
                .map(p -> p.getType().asString())
                .collect(Collectors.joining(","));
        return decl.getNameAsString() + "(" + params + ")";
    }

    /**
     * Single AST walk collecting both MethodInfo and CallEdge -- combined
     * into one visitor (rather than two separate passes) since both need
     * the same "which class am I currently inside" tracking, and
     * VoidVisitorAdapter does not expose that context for free.
     */
    private final class MethodAndCallVisitor extends VoidVisitorAdapter<Void> {
        final List<CodeEntities.MethodInfo> methods = new ArrayList<>();
        final List<CodeEntities.CallEdge> callEdges = new ArrayList<>();
        private final Set<String> knownClassIds;

        // ── Diagnostic counters (additive only, no logic changed) ──────────
        int diagTotal       = 0;  // total MethodCallExpr nodes visited that have a trackable caller
        int diagResolved    = 0;  // resolve() succeeded without throwing
        int diagCallEdge    = 0;  // resolved AND callee is in knownClassIds  → real CallEdge
        int diagExternal    = 0;  // resolved but callee outside our source   → correctly skipped
        int diagUnresolved  = 0;  // resolve() threw                          → correctly skipped
        // ───────────────────────────────────────────────────────────────────

        MethodAndCallVisitor(Set<String> knownClassIds) {
            this.knownClassIds = knownClassIds;
        }

        @Override
        public void visit(MethodDeclaration decl, Void arg) {
            super.visit(decl, arg);
            if (decl.getBegin().isEmpty() || decl.getEnd().isEmpty()) {
                return;
            }
            Optional<ClassOrInterfaceDeclaration> owner = decl.findAncestor(ClassOrInterfaceDeclaration.class);
            if (owner.isEmpty()) {
                return; // e.g. a method on an anonymous class -- out of scope for MVP symbol resolution
            }
            String classId = resolveQualifiedName(owner.get());
            if (!knownClassIds.contains(classId)) {
                return;
            }

            CodeEntities.MethodInfo info = new CodeEntities.MethodInfo();
            info.id = buildMethodId(classId, decl);
            info.class_id = classId;
            info.name = decl.getNameAsString();
            info.signature = buildSignatureLabel(decl);
            info.file_path = relativePath(decl.findCompilationUnit().orElseThrow());
            info.start_line = decl.getBegin().get().line;
            info.end_line = decl.getEnd().get().line;
            info.modifiers = decl.getModifiers().stream()
                    .map(m -> m.getKeyword().asString())
                    .collect(Collectors.toList());
            methods.add(info);

            // Caller id is derived once per enclosing method and reused for
            // every call found inside it in visit(MethodCallExpr, ...)
            // below via the ancestor lookup there -- see that method.
        }

        @Override
        public void visit(MethodCallExpr call, Void arg) {
            super.visit(call, arg);

            Optional<MethodDeclaration> callerDecl = call.findAncestor(MethodDeclaration.class);
            if (callerDecl.isEmpty()) {
                return; // call made outside any method body we track (e.g. a field initializer)
            }
            Optional<ClassOrInterfaceDeclaration> callerOwner =
                    callerDecl.get().findAncestor(ClassOrInterfaceDeclaration.class);
            if (callerOwner.isEmpty()) {
                return;
            }
            String callerClassId = resolveQualifiedName(callerOwner.get());
            if (!knownClassIds.contains(callerClassId)) {
                return;
            }
            String callerId = buildMethodId(callerClassId, callerDecl.get());

            // ── Diagnostic: count this call as "visited" ─────────────────
            diagTotal++;
            // ─────────────────────────────────────────────────────────────

            // Resolution can fail for many legitimate reasons (generics the
            // solver can't fully reason about, calls into code outside the
            // configured type solver's reach, lambdas/method references,
            // etc.) -- caught broadly on purpose so one unresolvable call
            // never aborts parsing of an otherwise-good file. Every such
            // skip is silent-by-design: a missing call edge degrades ACOR's
            // call-graph signal gracefully rather than failing the whole
            // indexing request.
            ResolvedMethodDeclaration resolved;
            try {
                resolved = call.resolve();
                diagResolved++; // ── Diagnostic: resolution succeeded ──────
            } catch (Exception unresolved) {
                diagUnresolved++; // ── Diagnostic: resolution threw ─────────
                return;
            }

            // Only calls that resolve back into OUR OWN parsed source
            // become graph edges (see class docstring / java_bridge.py:
            // calls into the JDK or third-party jars have no retrievable
            // source and would only create dangling nodes).
            if (!(resolved instanceof JavaParserMethodDeclaration)) {
                diagExternal++; // ── Diagnostic: resolved but external ────────
                return;
            }
            MethodDeclaration calleeDecl = ((JavaParserMethodDeclaration) resolved).getWrappedNode();
            Optional<ClassOrInterfaceDeclaration> calleeOwner =
                    calleeDecl.findAncestor(ClassOrInterfaceDeclaration.class);
            if (calleeOwner.isEmpty()) {
                diagExternal++; // ── Diagnostic: no owning class found ────────
                return;
            }
            String calleeClassId = resolveQualifiedName(calleeOwner.get());
            if (!knownClassIds.contains(calleeClassId)) {
                diagExternal++; // ── Diagnostic: callee outside our source ─────
                return;
            }
            String calleeId = buildMethodId(calleeClassId, calleeDecl);

            diagCallEdge++; // ── Diagnostic: became a real CallEdge ───────────
            callEdges.add(new CodeEntities.CallEdge(callerId, calleeId));
        }
    }
}