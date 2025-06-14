import re
import os
import sys
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional
import joblib
from sklearn.metrics.pairwise import cosine_similarity
import scipy.sparse as sparse
from sentence_transformers import SentenceTransformer
from difflib import SequenceMatcher
import logging
from utils.model_loader import get_sentence_transformer

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global singleton instance
_ats_scorer = None

def _check_section_headers(resume_text, section_keywords):
    """Helper function to check for section headers"""
    for keyword in section_keywords:
        section_header_patterns = [
            fr'\b({keyword})\s*:',  
            fr'\n\s*({keyword})\s*\n',  
            fr'\n\s*({keyword})\s*[^\n]*\n\s*[-•\*]'  
        ]

        for pattern in section_header_patterns:
            if re.search(pattern, resume_text, re.IGNORECASE):
                return True
    return False

def check_sections(resume_text):
    """Calculate score based on presence of essential resume sections with improved detection"""
    sections = {
        'experience': [
            'experience', 'work history', 'professional background', 'employment', 
            'work experience', 'career history', 'professional experience',
            'employment history', 'work background', 'professional journey'
        ],
        'education': [
            'education', 'academic', 'qualification', 'degree', 'university', 
            'college', 'school', 'certification', 'academic background',
            'educational background', 'academic qualifications', 'degrees',
            'certifications', 'training', 'courses'
        ],
        'skills': [
            'skills', 'abilities', 'competencies', 'expertise', 'proficiencies', 
            'technical skills', 'core competencies', 'technical expertise',
            'professional skills', 'key skills', 'skill set', 'capabilities',
            'technical proficiencies', 'areas of expertise'
        ],
        'summary': [
            'summary', 'profile', 'objective', 'about me', 'professional summary', 
            'career objective', 'professional profile', 'executive summary',
            'career summary', 'personal statement', 'professional overview',
            'career profile', 'professional statement'
        ],
        'projects': [
            'projects', 'portfolio', 'project experience', 'project history',
            'project work', 'project portfolio', 'project showcase',
            'project achievements', 'project highlights', 'project details'
        ],
        'achievements': [
            'achievements', 'accomplishments', 'awards', 'recognition',
            'honors', 'certifications', 'professional achievements',
            'key achievements', 'notable accomplishments', 'awards and recognition'
        ]
    }
    
    section_scores = {}
    for section_name, section_keywords in sections.items():
        # Check for section headers with improved patterns
        header_patterns = [
            fr'\b({keyword})\s*:',  
            fr'\n\s*({keyword})\s*\n',  
            fr'\n\s*({keyword})\s*[^\n]*\n\s*[-•\*]',
            fr'\n\s*({keyword})\s*[^\n]*\n\s*[A-Z]',
            fr'\n\s*({keyword})\s*[^\n]*\n\s*\d+\.'
        ]
        
        header_detected = False
        for keyword in section_keywords:
            for pattern in header_patterns:
                if re.search(pattern.format(keyword=keyword), resume_text, re.IGNORECASE):
                    header_detected = True
                    break
            if header_detected:
                break
        
        # Check for content with improved detection
        content_detected = False
        for keyword in section_keywords:
            if keyword in resume_text.lower():
                # Check if keyword is part of a meaningful section
                context = re.search(fr'\n.*?{keyword}.*?\n', resume_text, re.IGNORECASE)
                if context and len(context.group().strip()) > len(keyword) + 5:
                    content_detected = True
                    break
        
        # Calculate section score
        if header_detected:
            section_scores[section_name] = 1.0
        elif content_detected:
            section_scores[section_name] = 0.7
        else:
            section_scores[section_name] = 0.0
    
    # Calculate weighted total score
    weights = {
        'experience': 0.25,
        'education': 0.20,
        'skills': 0.20,
        'summary': 0.15,
        'projects': 0.10,
        'achievements': 0.10
    }
    
    total_score = sum(section_scores.get(section, 0) * weight 
                     for section, weight in weights.items())
    
    return total_score

def check_keywords(resume_text, job_keywords):
    """Calculate score based on keyword matches with improved relevance detection"""
    if not job_keywords:
        return 0
    
    # Preprocess resume text for better matching
    clean_resume = re.sub(r'[.,;:!?()\[\]{}]', ' ', resume_text)
    clean_resume = re.sub(r'\s+', ' ', clean_resume).strip()
    
    # Enhanced keyword importance weights
    keyword_importance = {}
    for kw in job_keywords:
        base_weight = 1.0
        
        # Technical skills get higher weight
        if any(tech in kw.lower() for tech in [
            'python', 'java', 'javascript', 'react', 'aws', 'cloud', 'ml', 'ai',
            'docker', 'kubernetes', 'sql', 'nosql', 'devops', 'security'
        ]):
            base_weight += 0.5
        
        # Framework and library skills
        if any(fw in kw.lower() for fw in [
            'react', 'angular', 'vue', 'django', 'flask', 'spring', 'express',
            'tensorflow', 'pytorch', 'scikit-learn'
        ]):
            base_weight += 0.3
        
        # Cloud and infrastructure skills
        if any(cloud in kw.lower() for cloud in [
            'aws', 'azure', 'gcp', 'cloud', 's3', 'ec2', 'lambda', 'kubernetes',
            'docker', 'terraform'
        ]):
            base_weight += 0.4
        
        keyword_importance[kw.lower()] = base_weight
    
    total_weight = sum(keyword_importance.values())
    
    # Enhanced keyword matching
    matches = {}
    for kw, weight in keyword_importance.items():
        # Exact match
        if f" {kw} " in f" {clean_resume} ":
            matches[kw] = weight
            continue
        
        # Contextual match for multi-word keywords
        if ' ' in kw:
            kw_parts = kw.split()
            
            # All parts present in close proximity
            if all(part in clean_resume for part in kw_parts if len(part) > 3):
                # Check if parts are within 5 words of each other
                parts_positions = [clean_resume.find(part) for part in kw_parts if len(part) > 3]
                if all(pos != -1 for pos in parts_positions):
                    max_distance = max(parts_positions) - min(parts_positions)
                    if max_distance < 50:  # Words are close to each other
                        matches[kw] = weight * 0.9
                        continue
            
            # Most parts present
            if len(kw_parts) > 2 and sum(1 for part in kw_parts if part in clean_resume and len(part) > 3) >= len(kw_parts) * 0.7:
                matches[kw] = weight * 0.7
                continue
        
        # Fuzzy match for single words
        if len(kw.split()) == 1 and len(kw) > 3:
            words = clean_resume.split()
            for word in words:
                if len(word) > 3 and get_skill_similarity(kw, word) > 0.8:
                    matches[kw] = weight * 0.6
                    break
    
    # Calculate weighted score with density bonus
    if total_weight == 0:
        return 0
    
    weighted_score = sum(matches.values()) / total_weight
    
    # Enhanced density bonus
    keyword_density = len(matches) / max(1, len(job_keywords))
    density_bonus = min(0.3, keyword_density * 0.5)  # Up to 30% bonus
    
    # Context bonus
    context_bonus = 0.0
    if len(matches) > 0:
        # Check if matched keywords appear in relevant sections
        relevant_sections = ['experience', 'skills', 'projects']
        for section in relevant_sections:
            section_match = re.search(fr'\n\s*{section}.*?\n', resume_text, re.IGNORECASE)
            if section_match:
                section_text = resume_text[section_match.start():section_match.end()].lower()
                section_matches = sum(1 for kw in matches if kw in section_text)
                if section_matches > 0:
                    context_bonus += 0.1  # 10% bonus per relevant section
    
    return min(1.0, weighted_score + density_bonus + context_bonus)

def get_skill_similarity(skill1: str, skill2: str) -> float:
    """Calculate similarity between two skills using SequenceMatcher."""
    return SequenceMatcher(None, skill1.lower(), skill2.lower()).ratio()

def check_formatting(resume_text):
    """Calculate score based on resume formatting with improved analysis"""
    format_score = 0
    
    # 1. Bullet points - comprehensive detection
    bullet_count = resume_text.count("•") + resume_text.count("- ") + resume_text.count("* ")
    numbered_bullets = len(re.findall(r'\n\s*\d+\.', resume_text))
    total_bullets = bullet_count + numbered_bullets
    
    # Scale bullet points score
    if total_bullets >= 10:
        format_score += 0.35  
    elif total_bullets >= 5:
        format_score += 0.25
    elif total_bullets > 0:
        format_score += 0.15
    
    # 2. Section headers detection 
    header_patterns = [
        r'(\n[A-Z][A-Z\s]+:)',  
        r'(\n[A-Z][A-Z\s]+\n)',  
        r'(\n[A-Z][a-z]+\s[A-Z][a-z]+:)',
        r'(\n\s*\d+\.\s*[A-Z][a-z]+)'  
    ]
    
    headers_count = 0
    for pattern in header_patterns:
        headers_count += len(re.findall(pattern, resume_text))
    
    # Scale headers score
    if headers_count >= 5:
        format_score += 0.35
    elif headers_count >= 3:
        format_score += 0.25
    elif headers_count > 0:
        format_score += 0.15
    
    # 3. Consistent date formatting
    date_patterns = [
        r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}\b',  
        r'\b\d{2}/\d{2}/\d{4}\b',  
        r'\b\d{4}-\d{2}-\d{2}\b',  #
        r'\b\d{4}\s*-\s*(?:Present|Current|Now)\b' 
    ]
    
    date_formats_found = 0
    for pattern in date_patterns:
        if re.search(pattern, resume_text):
            date_formats_found += 1

    date_format_score = min(0.15, date_formats_found * 0.05)
    format_score += date_format_score
    
    # 4. Contact information and links
    contact_patterns = [
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
        r'\b(?:\+\d{1,3}\s?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}\b',  # Phone
        r'linkedin\.com/in/[a-zA-Z0-9_-]+',  # LinkedIn
        r'github\.com/[a-zA-Z0-9_-]+'  # GitHub
    ]
    
    contact_score = 0
    for pattern in contact_patterns:
        if re.search(pattern, resume_text):
            contact_score += 0.05
    
    format_score += min(0.15, contact_score)
    
    return min(format_score, 1.0)

def check_context_relevance(resume_text, job_keywords):
    """Analyze the contextual relevance of keywords"""
    if not job_keywords:
        return 0

    context_score = 0
    key_sections = ['experience', 'project', 'skill', 'education']

    for section in key_sections:
       
        section_match = re.search(fr'\n\s*{section}.*?\n', resume_text, re.IGNORECASE)
        if not section_match:
            continue
            
        section_start = section_match.start()
        next_section_match = re.search(r'\n\s*[A-Z][A-Z\s]+\s*(?::|$)', resume_text[section_start+1:], re.IGNORECASE)
        
        if next_section_match:
            section_end = section_start + 1 + next_section_match.start()
        else:
            section_end = len(resume_text)
            
        section_text = resume_text[section_start:section_end].lower()
       
        section_keywords = sum(1 for kw in job_keywords if kw.lower() in section_text)
        
        # Add to context score based on section relevance
        weight = 0.4 if section in ['experience', 'skill'] else 0.2
        context_score += (section_keywords / len(job_keywords)) * weight
    
    return min(context_score, 1.0)

def ats_score(resume_text, job_keywords):
    """Calculate ATS compatibility score for a resume with enhanced algorithms"""
    if not resume_text or not job_keywords:
        return 0.0
    
    resume_text = resume_text.lower()
    job_keywords = [kw.strip() for kw in job_keywords if kw.strip()]
    
    # Calculate component scores
    section_score = check_sections(resume_text)
    keyword_score = check_keywords(resume_text, job_keywords)
    format_score = check_formatting(resume_text)
    context_score = check_context_relevance(resume_text, job_keywords)
    
    # Calculate final score with optimized weights
    final_score = (
        section_score * 0.25 + 
        keyword_score * 0.35 + 
        format_score * 0.20 + 
        context_score * 0.20
    )
    
    return round(final_score * 100, 2)

class ATSScorer:
    def __init__(self):
        """Initialize the ATS scorer."""
        self.model = None
        self.initialized = False
        self._initialize_model()

    def _initialize_model(self):
        """Initialize the sentence transformer model."""
        try:
            logger.info("Getting sentence transformer model...")
            self.model = get_sentence_transformer()
            self.initialized = True
        except Exception as e:
            logger.error(f"Error initializing ATS scorer: {str(e)}")
            self.initialized = False

    def calculate_ats_score(self, resume_text: str, job_description: str) -> Dict[str, Any]:
        """
        Calculate ATS score for a resume against a job description.
        
        Args:
            resume_text: The resume text to analyze
            job_description: The job description text
            
        Returns:
            Dictionary containing ATS score and analysis
        """
        if not self.initialized:
            logger.warning("ATS scorer not initialized, using basic scoring")
            return self._basic_ats_score(resume_text, job_description)

        try:
            # Get embeddings in a single batch
            texts = [resume_text, job_description]
            embeddings = self.model.encode(texts, show_progress_bar=False)
            resume_embedding, job_embedding = embeddings

            # Calculate similarity
            similarity = cosine_similarity([resume_embedding], [job_embedding])[0][0]
            ats_score = float(similarity * 100)

            # Analyze format and content in parallel
            format_analysis = self._analyze_format(resume_text)
            content_analysis = self._analyze_content(resume_text, job_description)

            return {
                'ats_score': ats_score,
                'format_analysis': format_analysis,
                'content_analysis': content_analysis
            }
        except Exception as e:
            logger.error(f"Error calculating ATS score: {str(e)}")
            return self._basic_ats_score(resume_text, job_description)

    def _basic_ats_score(self, resume_text: str, job_description: str) -> Dict[str, Any]:
        """Calculate basic ATS score when model is not available."""
        # Simple keyword matching
        resume_words = set(resume_text.lower().split())
        job_words = set(job_description.lower().split())
        
        # Calculate basic match score
        common_words = resume_words.intersection(job_words)
        match_score = len(common_words) / len(job_words) * 100 if job_words else 0

        return {
            'ats_score': match_score * 0.8,  # ATS score is typically lower than match score
            'format_analysis': {
                'score': 50,
                'issues': ['Basic analysis only'],
                'suggestions': ['Try again later for detailed format analysis']
            },
            'content_analysis': {
                'keyword_match': len(common_words),
                'missing_keywords': list(job_words - resume_words),
                'suggestions': ['Add missing keywords to improve ATS score']
            }
        }

    def _analyze_format(self, resume_text: str) -> Dict[str, Any]:
        """Analyze resume format and structure."""
        # Basic format analysis
        sections = ['education', 'experience', 'skills', 'projects']
        found_sections = [section for section in sections if section in resume_text.lower()]
        
        score = len(found_sections) / len(sections) * 100
        
        issues = []
        if len(found_sections) < len(sections):
            missing = set(sections) - set(found_sections)
            issues.append(f"Missing sections: {', '.join(missing)}")
        
        suggestions = [
            "Ensure all major sections are present",
            "Use consistent formatting throughout",
            "Include clear section headers"
        ]
        
        return {
            'score': score,
            'issues': issues,
            'suggestions': suggestions
        }

    def _analyze_content(self, resume_text: str, job_description: str) -> Dict[str, Any]:
        """Analyze resume content against job description."""
        # Extract keywords
        resume_words = set(resume_text.lower().split())
        job_words = set(job_description.lower().split())
        
        # Find matching and missing keywords
        matching_keywords = resume_words.intersection(job_words)
        missing_keywords = job_words - resume_words
        
        # Generate suggestions
        suggestions = []
        if missing_keywords:
            suggestions.append(f"Add these keywords: {', '.join(list(missing_keywords)[:5])}")
        
        return {
            'keyword_match': len(matching_keywords),
            'missing_keywords': list(missing_keywords),
            'suggestions': suggestions
        }

def get_ats_scorer() -> ATSScorer:
    """Get the global ATS scorer instance."""
    global _ats_scorer
    if _ats_scorer is None:
        _ats_scorer = ATSScorer()
    return _ats_scorer

def calculate_ats_score(resume_text: str, job_description: str) -> Dict[str, Any]:
    """Calculate ATS score and provide detailed analysis."""
    try:
        # Get the global ATS scorer instance
        scorer = get_ats_scorer()
        
        # Calculate scores
        result = scorer.calculate_ats_score(resume_text, job_description)
        
        # Add additional analysis
        result.update({
            'strengths': identify_strengths(resume_text, job_description),
            'weaknesses': identify_weaknesses(resume_text, job_description),
            'improvements': suggest_improvements(resume_text, job_description)
        })
        
        return result
        
    except Exception as e:
        logger.error(f"Error calculating ATS score: {str(e)}")
        return {
            'ats_score': 0,
            'format_analysis': {'score': 0, 'issues': [], 'suggestions': []},
            'content_analysis': {'keyword_match': 0, 'missing_keywords': [], 'suggestions': []},
            'strengths': [],
            'weaknesses': [],
            'improvements': []
        }

def identify_strengths(resume_text: str, job_description: str) -> List[str]:
    """Identify strengths in the resume that match job requirements."""
    try:
        strengths = []
        
        # Check for required skills match
        required_skills = extract_required_skills(job_description)
        resume_skills = extract_skills(resume_text)
        matching_skills = [skill for skill in required_skills if skill in resume_skills]
        if matching_skills:
            strengths.append(f"Strong match with required skills: {', '.join(matching_skills)}")
        
        # Check for experience level match
        exp_level = extract_experience_level(job_description)
        resume_exp = extract_experience_years(resume_text)
        if resume_exp >= exp_level:
            strengths.append(f"Meets or exceeds required experience level ({exp_level} years)")
        
        # Check for education match
        edu_req = extract_education_requirements(job_description)
        resume_edu = extract_education(resume_text)
        if any(edu in resume_edu for edu in edu_req):
            strengths.append("Meets education requirements")
        
        # Check for format
        if is_well_formatted(resume_text):
            strengths.append("Well-formatted resume with clear sections")
        
        return strengths
        
    except Exception as e:
        logger.error(f"Error identifying strengths: {str(e)}")
        return []

def identify_weaknesses(resume_text: str, job_description: str) -> List[str]:
    """Identify weaknesses in the resume compared to job requirements."""
    try:
        weaknesses = []
        
        # Check for missing required skills
        required_skills = extract_required_skills(job_description)
        resume_skills = extract_skills(resume_text)
        missing_skills = [skill for skill in required_skills if skill not in resume_skills]
        if missing_skills:
            weaknesses.append(f"Missing required skills: {', '.join(missing_skills)}")
        
        # Check for experience level
        exp_level = extract_experience_level(job_description)
        resume_exp = extract_experience_years(resume_text)
        if resume_exp < exp_level:
            weaknesses.append(f"Below required experience level ({exp_level} years)")
        
        # Check for education
        edu_req = extract_education_requirements(job_description)
        resume_edu = extract_education(resume_text)
        if not any(edu in resume_edu for edu in edu_req):
            weaknesses.append("Does not meet education requirements")
        
        # Check for format issues
        if not is_well_formatted(resume_text):
            weaknesses.append("Resume format could be improved")
        
        return weaknesses
        
    except Exception as e:
        logger.error(f"Error identifying weaknesses: {str(e)}")
        return []

def suggest_improvements(resume_text: str, job_description: str) -> List[str]:
    """Suggest specific improvements for the resume."""
    try:
        improvements = []
        
        # Skills improvements
        required_skills = extract_required_skills(job_description)
        resume_skills = extract_skills(resume_text)
        missing_skills = [skill for skill in required_skills if skill not in resume_skills]
        if missing_skills:
            improvements.append(f"Add missing required skills: {', '.join(missing_skills)}")
        
        # Experience improvements
        exp_level = extract_experience_level(job_description)
        resume_exp = extract_experience_years(resume_text)
        if resume_exp < exp_level:
            improvements.append(f"Highlight relevant experience to meet {exp_level} years requirement")
        
        # Education improvements
        edu_req = extract_education_requirements(job_description)
        resume_edu = extract_education(resume_text)
        if not any(edu in resume_edu for edu in edu_req):
            improvements.append(f"Consider adding {', '.join(edu_req)} education")
        
        # Format improvements
        if not is_well_formatted(resume_text):
            improvements.append("Improve resume formatting with clear sections and bullet points")
        
        return improvements
        
    except Exception as e:
        logger.error(f"Error suggesting improvements: {str(e)}")
        return []
