'''
package for generate word indexes to be attacked in a sentence
For insert, check
For delete,
For substitute, two method: unk(replace each word to <unk>) and pos_tag
'''
import math
import torch
import numpy as np
from dpattack.cmds.zeng.blackbox.constant import CONSTANT
from dpattack.utils.parser_helper import is_chars_judger
from dpattack.libs.luna.pytorch import cast_list

class AttackIndex(object):
    def __init__(self, config):
        self.revised_rate = config.revised_rate

    def get_attack_index(self, *args, **kwargs):
        pass

    def get_number(self, revised_rate, length):
        number = math.floor(revised_rate * length)
        if number == 0:
            number = 1
        return number

    def get_random_index_by_length_rate(self, index, revised_rate, length):
        number = self.get_number(revised_rate, length)
        if len(index) <= number:
            return index
        else:
            return np.random.choice(index, number)


class AttackIndexInserting(AttackIndex):
    def __init__(self, config):
        super(AttackIndexInserting, self).__init__(config)

    def get_attack_index(self, seqs, tags, arcs):
        index = []
        length = len(tags)
        for i in range(length):
            if tags[i] in CONSTANT.NOUN_TAG:
                # current index is a NN, check the word before it
                if self.check_noun(tags, i):
                    index.append(i - 1)
            elif tags[i].startswith(CONSTANT.VERB_TAG):
                # current index is a VB, check whether this VB is modified by a RB
                if self.check_verb(seqs[i-1], tags, arcs, i):
                    index.append(i)
        index = list(set(index))
        return index
        #return self.get_random_index_by_length_rate(index, self.revised_rate, length)

    def check_noun(self, tags, i):
        if i == 0:
            return True
        else:
            tag_before_word_i = tags[i-1]
            if not tag_before_word_i.startswith(CONSTANT.NOUN_TAG[0]) and not tag_before_word_i.startswith(CONSTANT.ADJ_TAG):
                return True
            return False

    def check_verb(self, verb, tags, arcs,i):
        if verb in CONSTANT.AUXILIARY_VERB:
            return False
        for tag, arc in zip(tags, arcs):
            if tag.startswith(CONSTANT.ADV_TAG) and arc == i:
                return False
        return True


class AttackIndexDeleting(AttackIndex):
    def __init__(self, config):
        super(AttackIndexDeleting, self).__init__(config)

    def get_attack_index(self, tags, arcs):
        index = []
        length = len(tags)
        for i in range(length):
            if tags[i].startswith(CONSTANT.ADJ_TAG) or tags[i].startswith(CONSTANT.ADV_TAG):
                if self.check_modifier(arcs,i):
                    index.append(i)
        return index

    def check_modifier(self, arcs, index):
        for arc in arcs:
            if arc == index:
                return False
        return True

class AttackIndexUnkReplacement(AttackIndex):
    def __init__(self, config, vocab = None, parser = None):
        super(AttackIndexUnkReplacement, self).__init__(config)

        self.parser = parser
        self.vocab = vocab
        self.unk_chars = self.get_unk_chars_idx(self.vocab.UNK)

    def get_attack_index(self, seqs, seq_idx, tags, tag_idx, chars, arcs, mask):
        length = torch.sum(mask).item()
        index_to_be_replace = cast_list(mask.squeeze(0).nonzero())
        # for metric when change a word to <unk>
        # change each word to <unk> in turn, taking the worst case.
        # For a seq_index [<ROOT>   1   2   3   ,   5]
        # seq_idx_unk is
        #  [[<ROOT>    <unk>    2   3   ,   5]
        #   [<ROOT>    1    <unk>   3   ,   5]
        #   [<ROOT>    1    2   <unk>   ,   5]
        #   [<ROOT>    1    2   3   ,   <unk>]]
        seq_idx_unk = self.generate_unk_seqs(seq_idx, length, index_to_be_replace)
        if is_chars_judger(self.parser):
            char_idx_unk = self.generate_unk_chars(chars, length, index_to_be_replace)
            score_arc, score_rel = self.parser.forward(seq_idx_unk, char_idx_unk)
        else:
            tag_idx_unk = self.generate_unk_tags(tag_idx, length)
            score_arc, score_rel = self.parser.forward(seq_idx_unk, tag_idx_unk)
        pred_arc = score_arc.argmax(dim=-1)
        non_equal_numbers = self.calculate_non_equal_numbers(pred_arc[:,mask.squeeze(0)], arcs[mask])
        sorted_index = sorted(range(length), key=lambda k: non_equal_numbers[k], reverse=True)
        number = self.get_number(self.revised_rate, length)
        return [index_to_be_replace[index] for index in sorted_index[:number]]

    def generate_unk_seqs(self, seq, length, index_to_be_replace):
        '''
        :param seq: seq_idx [<ROOT>   1   2   3   4   5], shape: [length + 1]
        :param length: sentence length
        :return:
        # for metric when change a word to <unk>
        # change each word to <unk> in turn, taking the worst case.
        # For a seq_index [<ROOT>   1   2   3   ,   5]
        # seq_idx_unk is
        #  [[<ROOT>    <unk>    2   3   ,   5]
        #   [<ROOT>    1    <unk>   3   ,   5]
        #   [<ROOT>    1    2   <unk>   ,   5]
        #   [<ROOT>    1    2   3   ,   <unk>]]
            shape: [length, length + 1]
        '''
        unk_seqs = seq.repeat(length, 1)
        for count, index in enumerate(index_to_be_replace):
            unk_seqs[count, index] = self.vocab.unk_index
        return unk_seqs

    def generate_unk_tags(self, tag, length):
        return tag.repeat(length, 1)

    def generate_unk_chars(self, char, length, index_to_be_replace):
        unk_chars = char.repeat(length, 1, 1)
        for count, index in enumerate(index_to_be_replace):
            unk_chars[count, index] = self.unk_chars
        return unk_chars

    def calculate_non_equal_numbers(self, pred_arc, gold_arc):
        '''
        :param pred_arc: pred arc 
        :param gold_arc: gold arc
        :return: the error numbers list
        '''
        non_equal_numbers = [torch.sum(torch.ne(arc, gold_arc)).item() for arc in pred_arc]
        return non_equal_numbers

    def get_unk_chars_idx(self, UNK_TOKEN):
        unk_chars = self.vocab.char2id([UNK_TOKEN]).squeeze(0)
        if torch.cuda.is_available():
            unk_chars = unk_chars.cuda()
        return unk_chars

class AttackIndexPosTag(AttackIndex):
    def __init__(self, config):
        super(AttackIndexPosTag, self).__init__(config)
        self.pos_tag = config.blackbox_pos_tag

    def get_attack_index(self, seqs, seq_idx, tags, tag_idx, chars, arcs, mask):
        index = [index - 1 for index, tag in enumerate(tags) if tag.startswith(self.pos_tag)]
        return self.get_random_index_by_length_rate(index, self.revised_rate, len(tags))
