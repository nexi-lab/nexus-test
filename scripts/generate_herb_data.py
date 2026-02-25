#!/usr/bin/env python3
"""Generate HERB (Hypothetical Enterprise Reference Benchmark) data.

Produces deterministic enterprise-context test data for memory/003 semantic search tests.

Output:
    benchmarks/herb/enterprise-context/products.jsonl   (30 products)
    benchmarks/herb/enterprise-context/employees.jsonl   (530 employees)
    benchmarks/herb/enterprise-context/customers.jsonl   (120 customers)

Usage:
    uv run python scripts/generate_herb_data.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

# Deterministic seed for reproducible data
RNG = random.Random(42)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "herb" / "enterprise-context"

# ---------------------------------------------------------------------------
# Domain vocabularies
# ---------------------------------------------------------------------------

DEPARTMENTS = [
    "Engineering", "Sales", "Marketing", "Finance", "HR",
    "Operations", "Legal", "Product", "Design", "Data Science",
    "Security", "DevOps", "QA", "Customer Success", "Support",
]

TITLES = [
    "Software Engineer", "Senior Software Engineer", "Staff Engineer",
    "Principal Engineer", "Engineering Manager", "Director of Engineering",
    "VP of Engineering", "Sales Representative", "Account Executive",
    "Sales Manager", "Marketing Analyst", "Content Strategist",
    "Product Manager", "Senior Product Manager", "UX Designer",
    "Data Scientist", "ML Engineer", "Security Analyst",
    "DevOps Engineer", "QA Engineer", "Technical Writer",
    "Customer Success Manager", "Support Engineer", "Financial Analyst",
    "HR Business Partner", "Legal Counsel", "Operations Manager",
]

FIRST_NAMES = [
    "Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Henry",
    "Iris", "Jack", "Karen", "Leo", "Mia", "Nathan", "Olivia", "Paul",
    "Quinn", "Rachel", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xavier",
    "Yuki", "Zara", "Amir", "Bianca", "Chen", "Diana", "Erik", "Fatima",
    "George", "Hana", "Ivan", "Julia", "Kenji", "Lena", "Marco", "Nadia",
    "Oscar", "Priya", "Raj", "Sofia", "Tomás", "Ursula", "Wei", "Xena",
    "Yusuf", "Zoe",
]

LAST_NAMES = [
    "Anderson", "Brown", "Chen", "Davis", "Evans", "Fischer", "Garcia",
    "Hernandez", "Ibrahim", "Johnson", "Kim", "Lee", "Martinez", "Nguyen",
    "O'Brien", "Patel", "Quinn", "Rodriguez", "Smith", "Tanaka",
    "Ueda", "Vargas", "Wang", "Xu", "Yamamoto", "Zhang", "Adams",
    "Baker", "Clark", "Diaz", "Edwards", "Foster", "Green", "Harris",
    "Jackson", "Kumar", "Lopez", "Miller", "Nelson", "Olsen", "Park",
    "Reed", "Sanders", "Taylor", "Thompson", "Walker", "White", "Young",
]

LOCATIONS = [
    "San Francisco, CA", "New York, NY", "Austin, TX", "Seattle, WA",
    "Boston, MA", "Chicago, IL", "Denver, CO", "Portland, OR",
    "Los Angeles, CA", "Atlanta, GA", "London, UK", "Berlin, DE",
    "Tokyo, JP", "Singapore, SG", "Sydney, AU",
]

PRODUCT_CATEGORIES = [
    "Analytics", "Infrastructure", "Security", "Developer Tools",
    "Collaboration", "Data Platform",
]

PRODUCT_ADJECTIVES = [
    "Cloud", "Enterprise", "Advanced", "Smart", "Unified", "Automated",
]

PRODUCT_NOUNS = [
    "Hub", "Suite", "Engine", "Platform", "Gateway", "Monitor",
    "Orchestrator", "Analyzer", "Dashboard", "Pipeline",
]

INDUSTRIES = [
    "Technology", "Healthcare", "Finance", "Retail", "Manufacturing",
    "Education", "Media", "Telecommunications", "Energy", "Transportation",
    "Real Estate", "Agriculture",
]

COMPANY_SUFFIXES = [
    "Corp", "Inc", "Ltd", "Group", "Solutions", "Technologies",
    "Systems", "Global", "Partners", "Industries",
]


def _generate_employees(count: int) -> list[dict]:
    """Generate employee records."""
    employees = []
    for i in range(count):
        first = RNG.choice(FIRST_NAMES)
        last = RNG.choice(LAST_NAMES)
        dept = RNG.choice(DEPARTMENTS)
        title = RNG.choice(TITLES)
        location = RNG.choice(LOCATIONS)
        years = RNG.randint(1, 20)
        salary_band = RNG.choice(["L3", "L4", "L5", "L6", "L7", "L8"])

        # Build a rich content string for semantic search
        skills = RNG.sample(
            [
                "Python", "Go", "Rust", "TypeScript", "Java", "Kubernetes",
                "PostgreSQL", "Redis", "GraphQL", "React", "machine learning",
                "distributed systems", "microservices", "CI/CD", "AWS",
                "data pipelines", "security auditing", "API design",
                "performance optimization", "team leadership",
            ],
            k=RNG.randint(2, 5),
        )
        projects = RNG.sample(
            [
                "Project Neptune", "Project Aurora", "Project Titan",
                "Project Horizon", "Project Phoenix", "Project Atlas",
                "Project Zenith", "Project Quantum", "Project Nexus",
                "Project Omega",
            ],
            k=RNG.randint(1, 3),
        )

        content = (
            f"{first} {last} is a {title} in the {dept} department "
            f"based in {location}. They have {years} years of experience "
            f"and are currently at level {salary_band}. "
            f"Their key skills include {', '.join(skills)}. "
            f"They are contributing to {', '.join(projects)}."
        )

        employees.append({
            "id": f"emp-{i + 1:04d}",
            "type": "employee",
            "name": f"{first} {last}",
            "department": dept,
            "title": title,
            "location": location,
            "years_experience": years,
            "level": salary_band,
            "skills": skills,
            "projects": projects,
            "content": content,
        })
    return employees


def _generate_products(count: int) -> list[dict]:
    """Generate product records."""
    products = []
    for i in range(count):
        adj = RNG.choice(PRODUCT_ADJECTIVES)
        noun = RNG.choice(PRODUCT_NOUNS)
        category = RNG.choice(PRODUCT_CATEGORIES)
        name = f"{adj} {noun}"
        version = f"{RNG.randint(1, 5)}.{RNG.randint(0, 9)}"
        revenue_m = round(RNG.uniform(0.5, 50.0), 1)
        customers_count = RNG.randint(10, 5000)

        features = RNG.sample(
            [
                "real-time analytics", "SSO integration", "RBAC",
                "audit logging", "API gateway", "webhook support",
                "data encryption at rest", "multi-region deployment",
                "auto-scaling", "custom dashboards", "alerting",
                "data export", "team collaboration", "version control",
            ],
            k=RNG.randint(3, 6),
        )

        content = (
            f"{name} is a {category.lower()} product (v{version}) "
            f"generating ${revenue_m}M ARR with {customers_count} active customers. "
            f"Key features include {', '.join(features)}."
        )

        products.append({
            "id": f"prod-{i + 1:03d}",
            "type": "product",
            "name": name,
            "category": category,
            "version": version,
            "arr_millions": revenue_m,
            "active_customers": customers_count,
            "features": features,
            "content": content,
        })
    return products


def _generate_customers(count: int) -> list[dict]:
    """Generate customer records."""
    customers = []
    for i in range(count):
        company = f"{RNG.choice(LAST_NAMES)} {RNG.choice(COMPANY_SUFFIXES)}"
        industry = RNG.choice(INDUSTRIES)
        size = RNG.choice(["startup", "mid-market", "enterprise"])
        employees_count = {
            "startup": RNG.randint(10, 100),
            "mid-market": RNG.randint(100, 5000),
            "enterprise": RNG.randint(5000, 100000),
        }[size]
        contract_value = round(RNG.uniform(5.0, 500.0), 1)
        satisfaction = round(RNG.uniform(3.0, 5.0), 1)
        products_used = RNG.randint(1, 4)

        use_cases = RNG.sample(
            [
                "data analytics", "security monitoring", "CI/CD automation",
                "customer support", "infrastructure management",
                "compliance reporting", "developer productivity",
                "cost optimization", "incident response",
            ],
            k=RNG.randint(1, 3),
        )

        content = (
            f"{company} is a {size} {industry.lower()} company "
            f"with {employees_count:,} employees. They use {products_used} "
            f"of our products with an annual contract value of ${contract_value}K. "
            f"Primary use cases: {', '.join(use_cases)}. "
            f"Customer satisfaction score: {satisfaction}/5.0."
        )

        customers.append({
            "id": f"cust-{i + 1:03d}",
            "type": "customer",
            "company": company,
            "industry": industry,
            "size": size,
            "employee_count": employees_count,
            "acv_thousands": contract_value,
            "satisfaction_score": satisfaction,
            "products_used": products_used,
            "use_cases": use_cases,
            "content": content,
        })
    return customers


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    datasets = {
        "employees.jsonl": _generate_employees(530),
        "products.jsonl": _generate_products(30),
        "customers.jsonl": _generate_customers(120),
    }

    for filename, records in datasets.items():
        path = OUTPUT_DIR / filename
        with path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"  {filename}: {len(records)} records -> {path}")

    # Summary markdown
    summary_path = OUTPUT_DIR / "README.md"
    summary_path.write_text(
        "# HERB Enterprise Context\n\n"
        "Hypothetical Enterprise Reference Benchmark data for memory/003 semantic search tests.\n\n"
        "## Contents\n\n"
        f"- `employees.jsonl` — {len(datasets['employees.jsonl'])} employee records\n"
        f"- `products.jsonl` — {len(datasets['products.jsonl'])} product records\n"
        f"- `customers.jsonl` — {len(datasets['customers.jsonl'])} customer records\n\n"
        "## Regeneration\n\n"
        "```bash\nuv run python scripts/generate_herb_data.py\n```\n\n"
        "Uses seed 42 for deterministic output.\n",
        encoding="utf-8",
    )
    print(f"  README.md -> {summary_path}")
    print(f"\nTotal: {sum(len(r) for r in datasets.values())} records generated.")


if __name__ == "__main__":
    main()
